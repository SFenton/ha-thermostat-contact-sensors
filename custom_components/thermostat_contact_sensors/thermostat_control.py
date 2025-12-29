"""Thermostat control logic for Thermostat Contact Sensors integration.

This module provides intelligent thermostat control based on room occupancy
and temperature readings. It manages when to turn the thermostat on/off based on:
- Active rooms (rooms occupied long enough to be considered for climate control)
- Temperature readings from sensors in those active rooms
- The thermostat's current HVAC mode and target temperature
- Cycle protection to prevent rapid on/off switching

Key concepts:
- "Satiated": A room is satiated when at least one temperature sensor in the room
  has reached the target temperature (considering the deadband)
- For HEAT mode: satiated when any sensor >= target - deadband
- For COOL mode: satiated when any sensor <= target + deadband
- For HEAT_COOL mode: both conditions must be met

The thermostat should be ON when any active room is not satiated.
The thermostat should be OFF when all active rooms are satiated.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_MIN_CYCLE_OFF_MINUTES,
    DEFAULT_MIN_CYCLE_ON_MINUTES,
    DEFAULT_TEMPERATURE_DEADBAND,
    DEFAULT_UNOCCUPIED_COOLING_THRESHOLD,
    DEFAULT_UNOCCUPIED_HEATING_THRESHOLD,
)
from .occupancy import AreaOccupancyState, RoomOccupancyTracker

_LOGGER = logging.getLogger(__name__)

# Storage version for thermostat controller state persistence
THERMOSTAT_STORAGE_VERSION = 1
THERMOSTAT_STORAGE_KEY = "thermostat_contact_sensors.thermostat_controller"

# Climate entity attributes
ATTR_TARGET_TEMP_HIGH = "target_temp_high"
ATTR_TARGET_TEMP_LOW = "target_temp_low"
ATTR_HVAC_MODE = "hvac_mode"
ATTR_CURRENT_TEMPERATURE = "current_temperature"


class ThermostatAction(Enum):
    """Actions the thermostat controller can take."""

    NONE = "none"  # No action needed
    TURN_ON = "turn_on"  # Thermostat should be turned on
    TURN_OFF = "turn_off"  # Thermostat should be turned off
    WAIT_CYCLE_ON = "wait_cycle_on"  # Want to turn off but waiting for min on time
    WAIT_CYCLE_OFF = "wait_cycle_off"  # Want to turn on but waiting for min off time


class SatiationReason(Enum):
    """Reasons why a room is or isn't satiated."""

    SATIATED = "satiated"  # Room has reached target temperature
    NOT_SATIATED = "not_satiated"  # Room hasn't reached target
    NO_TEMP_SENSORS = "no_temp_sensors"  # Room has no temperature sensors
    ALL_SENSORS_UNAVAILABLE = "all_sensors_unavailable"  # All sensors unavailable
    NO_TARGET_TEMP = "no_target_temp"  # Thermostat has no target temperature set


@dataclass
class RoomTemperatureState:
    """Temperature state for a single room/area."""

    area_id: str
    area_name: str
    temperature_sensors: list[str] = field(default_factory=list)

    # Current readings (entity_id -> temperature)
    sensor_readings: dict[str, float] = field(default_factory=dict)

    # Satiation state (for active rooms)
    is_satiated: bool = False
    satiation_reason: SatiationReason = SatiationReason.NO_TEMP_SENSORS

    # Critical state (for unoccupied rooms that are too cold/hot)
    is_critical: bool = False
    critical_reason: str | None = None

    # Whether this room is active (occupied long enough)
    is_active: bool = False

    # The sensor that determined satiation (closest to target)
    determining_sensor: str | None = None
    determining_temperature: float | None = None

    # Target temperature for distance calculations
    target_temperature: float | None = None

    @property
    def has_valid_readings(self) -> bool:
        """Return True if at least one sensor has a valid reading."""
        return len(self.sensor_readings) > 0

    @property
    def available_sensor_count(self) -> int:
        """Return the number of sensors with valid readings."""
        return len(self.sensor_readings)

    def get_closest_to_target(
        self, target: float, mode: HVACMode
    ) -> tuple[str | None, float | None]:
        """Get the sensor closest to the target temperature.

        For HEAT mode: returns the warmest sensor (closest to/above target)
        For COOL mode: returns the coolest sensor (closest to/below target)

        Returns:
            Tuple of (entity_id, temperature) or (None, None) if no readings.
        """
        if not self.sensor_readings:
            return None, None

        if mode == HVACMode.HEAT:
            # For heating, we want the warmest sensor
            return max(self.sensor_readings.items(), key=lambda x: x[1])
        elif mode == HVACMode.COOL:
            # For cooling, we want the coolest sensor
            return min(self.sensor_readings.items(), key=lambda x: x[1])
        else:
            # For heat_cool or other modes, return the one closest to target
            closest = min(
                self.sensor_readings.items(), key=lambda x: abs(x[1] - target)
            )
            return closest


@dataclass
class ThermostatState:
    """Current state of the thermostat and control decisions."""

    # Thermostat entity info
    thermostat_entity_id: str
    hvac_mode: HVACMode | None = None
    is_on: bool = False

    # Target temperatures
    target_temperature: float | None = None
    target_temp_high: float | None = None  # For heat_cool mode (cooling target)
    target_temp_low: float | None = None  # For heat_cool mode (heating target)

    # Room states (includes both active and critical rooms)
    room_states: dict[str, RoomTemperatureState] = field(default_factory=dict)

    # Overall state
    all_active_rooms_satiated: bool = False
    active_room_count: int = 0
    satiated_room_count: int = 0

    # Critical rooms (unoccupied but need conditioning)
    critical_room_count: int = 0

    # Cycle protection
    last_on_time: datetime | None = None
    last_off_time: datetime | None = None

    # Recommended action
    recommended_action: ThermostatAction = ThermostatAction.NONE
    action_reason: str = ""


def get_temperature_from_state(state: State | None) -> float | None:
    """Extract temperature value from a sensor state.

    Args:
        state: The state object for the temperature sensor.

    Returns:
        The temperature as a float, or None if unavailable/invalid.
    """
    if state is None:
        return None

    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None

    try:
        return float(state.state)
    except (ValueError, TypeError):
        return None


def is_room_satiated_for_heat(
    readings: dict[str, float], target: float, deadband: float
) -> tuple[bool, str | None, float | None]:
    """Check if a room is satiated for heating mode.

    A room is satiated for heating when ANY sensor reads at or above
    (target - deadband).

    Args:
        readings: Dict of sensor_id -> temperature.
        target: Target temperature.
        deadband: Temperature deadband/hysteresis.

    Returns:
        Tuple of (is_satiated, determining_sensor, determining_temp).
    """
    if not readings:
        return False, None, None

    threshold = target - deadband

    # Find the warmest sensor (most likely to be satiated)
    warmest_sensor, warmest_temp = max(readings.items(), key=lambda x: x[1])

    if warmest_temp >= threshold:
        return True, warmest_sensor, warmest_temp

    return False, warmest_sensor, warmest_temp


def is_room_satiated_for_cool(
    readings: dict[str, float], target: float, deadband: float
) -> tuple[bool, str | None, float | None]:
    """Check if a room is satiated for cooling mode.

    A room is satiated for cooling when ANY sensor reads at or below
    (target + deadband).

    Args:
        readings: Dict of sensor_id -> temperature.
        target: Target temperature.
        deadband: Temperature deadband/hysteresis.

    Returns:
        Tuple of (is_satiated, determining_sensor, determining_temp).
    """
    if not readings:
        return False, None, None

    threshold = target + deadband

    # Find the coolest sensor (most likely to be satiated)
    coolest_sensor, coolest_temp = min(readings.items(), key=lambda x: x[1])

    if coolest_temp <= threshold:
        return True, coolest_sensor, coolest_temp

    return False, coolest_sensor, coolest_temp


def is_room_satiated_for_heat_cool(
    readings: dict[str, float],
    target_low: float,
    target_high: float,
    deadband: float,
) -> tuple[bool, str | None, float | None]:
    """Check if a room is satiated for heat_cool (auto) mode.

    A room is satiated when:
    - Any sensor is at or above (target_low - deadband) for heating, AND
    - Any sensor is at or below (target_high + deadband) for cooling

    In practice, if any sensor is within the comfortable range, we're satiated.

    Args:
        readings: Dict of sensor_id -> temperature.
        target_low: Lower target (heating setpoint).
        target_high: Upper target (cooling setpoint).
        deadband: Temperature deadband/hysteresis.

    Returns:
        Tuple of (is_satiated, determining_sensor, determining_temp).
    """
    if not readings:
        return False, None, None

    heat_threshold = target_low - deadband
    cool_threshold = target_high + deadband

    # Find the sensor closest to the comfortable range
    # A room is satiated if any sensor is in the comfortable zone
    for sensor_id, temp in readings.items():
        if heat_threshold <= temp <= cool_threshold:
            return True, sensor_id, temp

    # Not satiated - return the sensor closest to the range
    def distance_to_range(temp: float) -> float:
        if temp < heat_threshold:
            return heat_threshold - temp
        elif temp > cool_threshold:
            return temp - cool_threshold
        return 0

    closest = min(readings.items(), key=lambda x: distance_to_range(x[1]))
    return False, closest[0], closest[1]


class ThermostatController:
    """Controller for thermostat based on room occupancy and temperatures.

    This class coordinates between:
    - Room occupancy tracking (which rooms are active)
    - Temperature sensor readings
    - Thermostat state and target temperatures
    - Cycle protection timers

    It determines when the thermostat should be on or off based on whether
    all active rooms have reached their target temperature.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        thermostat_entity_id: str,
        occupancy_tracker: RoomOccupancyTracker,
        entry_id: str | None = None,
        temperature_deadband: float = DEFAULT_TEMPERATURE_DEADBAND,
        min_cycle_on_minutes: int = DEFAULT_MIN_CYCLE_ON_MINUTES,
        min_cycle_off_minutes: int = DEFAULT_MIN_CYCLE_OFF_MINUTES,
        unoccupied_heating_threshold: float = DEFAULT_UNOCCUPIED_HEATING_THRESHOLD,
        unoccupied_cooling_threshold: float = DEFAULT_UNOCCUPIED_COOLING_THRESHOLD,
    ) -> None:
        """Initialize the thermostat controller.

        Args:
            hass: The Home Assistant instance.
            thermostat_entity_id: Entity ID of the thermostat to control.
            occupancy_tracker: RoomOccupancyTracker instance for occupancy data.
            entry_id: Config entry ID for storage key uniqueness.
            temperature_deadband: Temperature buffer to prevent cycling.
            min_cycle_on_minutes: Minimum time thermostat must stay on.
            min_cycle_off_minutes: Minimum time thermostat must stay off.
            unoccupied_heating_threshold: Degrees below heat target that triggers
                heating in unoccupied rooms.
            unoccupied_cooling_threshold: Degrees above cool target that triggers
                cooling in unoccupied rooms.
        """
        self.hass = hass
        self.thermostat_entity_id = thermostat_entity_id
        self.occupancy_tracker = occupancy_tracker

        self._temperature_deadband = temperature_deadband
        self._min_cycle_on_minutes = min_cycle_on_minutes
        self._min_cycle_off_minutes = min_cycle_off_minutes
        self._unoccupied_heating_threshold = unoccupied_heating_threshold
        self._unoccupied_cooling_threshold = unoccupied_cooling_threshold

        # State tracking
        self._is_paused_by_contact_sensors = False
        self._last_on_time: datetime | None = None
        self._last_off_time: datetime | None = None
        self._current_thermostat_on: bool = False
        self._we_turned_off: bool = False  # Track if integration turned off thermostat

        # Storage for persisting state across restarts
        if entry_id:
            self._store: Store | None = Store(
                hass,
                THERMOSTAT_STORAGE_VERSION,
                f"{THERMOSTAT_STORAGE_KEY}.{entry_id}",
            )
        else:
            self._store = None

        # Listeners
        self._unsub_thermostat_state_change: callable | None = None
        self._unsub_temp_sensor_state_change: callable | None = None
        self._update_callbacks: list[callable] = []

    @property
    def temperature_deadband(self) -> float:
        """Return the temperature deadband."""
        return self._temperature_deadband

    @temperature_deadband.setter
    def temperature_deadband(self, value: float) -> None:
        """Set the temperature deadband."""
        self._temperature_deadband = value

    @property
    def min_cycle_on_minutes(self) -> int:
        """Return minimum on-cycle time in minutes."""
        return self._min_cycle_on_minutes

    @min_cycle_on_minutes.setter
    def min_cycle_on_minutes(self, value: int) -> None:
        """Set minimum on-cycle time in minutes."""
        self._min_cycle_on_minutes = value

    @property
    def min_cycle_off_minutes(self) -> int:
        """Return minimum off-cycle time in minutes."""
        return self._min_cycle_off_minutes

    @min_cycle_off_minutes.setter
    def min_cycle_off_minutes(self, value: int) -> None:
        """Set minimum off-cycle time in minutes."""
        self._min_cycle_off_minutes = value

    @property
    def unoccupied_heating_threshold(self) -> float:
        """Return the unoccupied heating threshold."""
        return self._unoccupied_heating_threshold

    @unoccupied_heating_threshold.setter
    def unoccupied_heating_threshold(self, value: float) -> None:
        """Set the unoccupied heating threshold."""
        self._unoccupied_heating_threshold = value

    @property
    def unoccupied_cooling_threshold(self) -> float:
        """Return the unoccupied cooling threshold."""
        return self._unoccupied_cooling_threshold

    @unoccupied_cooling_threshold.setter
    def unoccupied_cooling_threshold(self, value: float) -> None:
        """Set the unoccupied cooling threshold."""
        self._unoccupied_cooling_threshold = value

    @property
    def is_paused_by_contact_sensors(self) -> bool:
        """Return whether thermostat is paused due to open contact sensors."""
        return self._is_paused_by_contact_sensors

    def set_paused_by_contact_sensors(self, paused: bool) -> None:
        """Set whether thermostat is paused due to open contact sensors.

        When paused by contact sensors, the thermostat controller will not
        take any actions - contact sensor pause takes priority.

        Args:
            paused: True if contact sensors have paused the thermostat.
        """
        self._is_paused_by_contact_sensors = paused
        _LOGGER.debug("Contact sensor pause state: %s", paused)

    def get_thermostat_state(self) -> tuple[HVACMode | None, bool]:
        """Get the current thermostat HVAC mode and on/off state.

        Returns:
            Tuple of (hvac_mode, is_on).
        """
        state = self.hass.states.get(self.thermostat_entity_id)
        if state is None:
            return None, False

        try:
            hvac_mode = HVACMode(state.state)
            is_on = hvac_mode != HVACMode.OFF
            return hvac_mode, is_on
        except ValueError:
            return None, False

    def get_target_temperatures(
        self,
    ) -> tuple[float | None, float | None, float | None]:
        """Get target temperatures from the thermostat.

        Returns:
            Tuple of (target_temperature, target_temp_low, target_temp_high).
            - target_temperature: Used for HEAT or COOL mode
            - target_temp_low: Heating setpoint for HEAT_COOL mode
            - target_temp_high: Cooling setpoint for HEAT_COOL mode
        """
        state = self.hass.states.get(self.thermostat_entity_id)
        if state is None:
            return None, None, None

        attrs = state.attributes

        target_temp = attrs.get(ATTR_TEMPERATURE)
        target_temp_low = attrs.get(ATTR_TARGET_TEMP_LOW)
        target_temp_high = attrs.get(ATTR_TARGET_TEMP_HIGH)

        # Convert to float if present
        if target_temp is not None:
            try:
                target_temp = float(target_temp)
            except (ValueError, TypeError):
                target_temp = None

        if target_temp_low is not None:
            try:
                target_temp_low = float(target_temp_low)
            except (ValueError, TypeError):
                target_temp_low = None

        if target_temp_high is not None:
            try:
                target_temp_high = float(target_temp_high)
            except (ValueError, TypeError):
                target_temp_high = None

        return target_temp, target_temp_low, target_temp_high

    def get_temperature_sensors_for_area(self, area_id: str) -> list[str]:
        """Get list of temperature sensor entity IDs for an area.

        This reads from the occupancy tracker's area configuration.

        Args:
            area_id: The area ID to look up.

        Returns:
            List of temperature sensor entity IDs.
        """
        # The temperature sensors are stored in the area config
        # We need to access them from the coordinator or config
        # For now, we'll return an empty list - this will be connected
        # when integrating with the main coordinator
        return []

    def evaluate_room_satiation(
        self,
        area: AreaOccupancyState,
        temperature_sensors: list[str],
        hvac_mode: HVACMode,
        target_temp: float | None,
        target_temp_low: float | None,
        target_temp_high: float | None,
    ) -> RoomTemperatureState:
        """Evaluate whether a room is satiated based on temperature readings.

        Args:
            area: The area occupancy state.
            temperature_sensors: List of temperature sensor entity IDs for this area.
            hvac_mode: Current HVAC mode.
            target_temp: Target temperature (for heat/cool modes).
            target_temp_low: Low target (for heat_cool mode).
            target_temp_high: High target (for heat_cool mode).

        Returns:
            RoomTemperatureState with satiation evaluation.
        """
        room_state = RoomTemperatureState(
            area_id=area.area_id,
            area_name=area.area_name,
            temperature_sensors=temperature_sensors,
        )

        # Collect temperature readings from all sensors
        for sensor_id in temperature_sensors:
            state = self.hass.states.get(sensor_id)
            temp = get_temperature_from_state(state)
            if temp is not None:
                room_state.sensor_readings[sensor_id] = temp

        # Handle no valid readings
        if not room_state.sensor_readings:
            if temperature_sensors:
                room_state.satiation_reason = SatiationReason.ALL_SENSORS_UNAVAILABLE
            else:
                room_state.satiation_reason = SatiationReason.NO_TEMP_SENSORS
            return room_state

        # Evaluate satiation based on HVAC mode
        if hvac_mode == HVACMode.HEAT:
            if target_temp is None:
                room_state.satiation_reason = SatiationReason.NO_TARGET_TEMP
                return room_state

            is_sat, sensor, temp = is_room_satiated_for_heat(
                room_state.sensor_readings, target_temp, self._temperature_deadband
            )
            room_state.is_satiated = is_sat
            room_state.determining_sensor = sensor
            room_state.determining_temperature = temp
            room_state.target_temperature = target_temp
            room_state.satiation_reason = (
                SatiationReason.SATIATED if is_sat else SatiationReason.NOT_SATIATED
            )

        elif hvac_mode == HVACMode.COOL:
            if target_temp is None:
                room_state.satiation_reason = SatiationReason.NO_TARGET_TEMP
                return room_state

            is_sat, sensor, temp = is_room_satiated_for_cool(
                room_state.sensor_readings, target_temp, self._temperature_deadband
            )
            room_state.is_satiated = is_sat
            room_state.determining_sensor = sensor
            room_state.determining_temperature = temp
            room_state.target_temperature = target_temp
            room_state.satiation_reason = (
                SatiationReason.SATIATED if is_sat else SatiationReason.NOT_SATIATED
            )

        elif hvac_mode == HVACMode.HEAT_COOL:
            if target_temp_low is None or target_temp_high is None:
                room_state.satiation_reason = SatiationReason.NO_TARGET_TEMP
                return room_state

            is_sat, sensor, temp = is_room_satiated_for_heat_cool(
                room_state.sensor_readings,
                target_temp_low,
                target_temp_high,
                self._temperature_deadband,
            )
            room_state.is_satiated = is_sat
            room_state.determining_sensor = sensor
            room_state.determining_temperature = temp
            # For heat_cool, use the midpoint as target for distance calculations
            room_state.target_temperature = (target_temp_low + target_temp_high) / 2
            room_state.satiation_reason = (
                SatiationReason.SATIATED if is_sat else SatiationReason.NOT_SATIATED
            )

        else:
            # For other modes (OFF, FAN_ONLY, etc.), consider satiated
            room_state.is_satiated = True
            room_state.satiation_reason = SatiationReason.SATIATED

        return room_state

    def evaluate_room_critical(
        self,
        area: AreaOccupancyState,
        temperature_sensors: list[str],
        hvac_mode: HVACMode,
        target_temp: float | None,
        target_temp_low: float | None,
        target_temp_high: float | None,
    ) -> RoomTemperatureState:
        """Evaluate whether an unoccupied room is in a critical temperature state.

        A room is critical when its temperature drops too far below the heating
        target or rises too far above the cooling target, even though the room
        is not actively occupied.

        Args:
            area: The area occupancy state.
            temperature_sensors: List of temperature sensor entity IDs for this area.
            hvac_mode: Current HVAC mode.
            target_temp: Target temperature (for heat/cool modes).
            target_temp_low: Low target (for heat_cool mode).
            target_temp_high: High target (for heat_cool mode).

        Returns:
            RoomTemperatureState with critical evaluation.
        """
        room_state = RoomTemperatureState(
            area_id=area.area_id,
            area_name=area.area_name,
            temperature_sensors=temperature_sensors,
            is_active=False,  # This method is for inactive rooms
        )

        # Collect temperature readings from all sensors
        for sensor_id in temperature_sensors:
            state = self.hass.states.get(sensor_id)
            temp = get_temperature_from_state(state)
            if temp is not None:
                room_state.sensor_readings[sensor_id] = temp

        # Handle no valid readings
        if not room_state.sensor_readings:
            return room_state

        # Use most favorable sensor (closest to target) - only critical if whole room is in trouble
        if hvac_mode == HVACMode.HEAT:
            if target_temp is None:
                return room_state

            # For heating, use the warmest sensor (most favorable)
            # Only critical if even the warmest spot is too cold
            warmest_sensor, warmest_temp = max(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            critical_threshold = target_temp - self._unoccupied_heating_threshold

            room_state.determining_sensor = warmest_sensor
            room_state.determining_temperature = warmest_temp

            if warmest_temp < critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {warmest_temp:.1f}° is {target_temp - warmest_temp:.1f}° "
                    f"below heat target {target_temp:.1f}° (threshold: {self._unoccupied_heating_threshold:.1f}°)"
                )

        elif hvac_mode == HVACMode.COOL:
            if target_temp is None:
                return room_state

            # For cooling, use the coolest sensor (most favorable)
            # Only critical if even the coolest spot is too hot
            coolest_sensor, coolest_temp = min(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            critical_threshold = target_temp + self._unoccupied_cooling_threshold

            room_state.determining_sensor = coolest_sensor
            room_state.determining_temperature = coolest_temp

            if coolest_temp > critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {coolest_temp:.1f}° is {coolest_temp - target_temp:.1f}° "
                    f"above cool target {target_temp:.1f}° (threshold: {self._unoccupied_cooling_threshold:.1f}°)"
                )

        elif hvac_mode == HVACMode.HEAT_COOL:
            if target_temp_low is None or target_temp_high is None:
                return room_state

            # Use most favorable sensors for each mode
            warmest_sensor, warmest_temp = max(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            coolest_sensor, coolest_temp = min(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )

            heat_critical_threshold = target_temp_low - self._unoccupied_heating_threshold
            cool_critical_threshold = target_temp_high + self._unoccupied_cooling_threshold

            # For heating critical: even warmest spot is too cold
            if warmest_temp < heat_critical_threshold:
                room_state.is_critical = True
                room_state.determining_sensor = warmest_sensor
                room_state.determining_temperature = warmest_temp
                room_state.critical_reason = (
                    f"Temperature {warmest_temp:.1f}° is {target_temp_low - warmest_temp:.1f}° "
                    f"below heat target {target_temp_low:.1f}° (threshold: {self._unoccupied_heating_threshold:.1f}°)"
                )
            # For cooling critical: even coolest spot is too hot
            elif coolest_temp > cool_critical_threshold:
                room_state.is_critical = True
                room_state.determining_sensor = coolest_sensor
                room_state.determining_temperature = coolest_temp
                room_state.critical_reason = (
                    f"Temperature {coolest_temp:.1f}° is {coolest_temp - target_temp_high:.1f}° "
                    f"above cool target {target_temp_high:.1f}° (threshold: {self._unoccupied_cooling_threshold:.1f}°)"
                )

        return room_state

    def can_turn_on(self, now: datetime | None = None) -> tuple[bool, str]:
        """Check if the thermostat can be turned on (cycle protection).

        Args:
            now: Current time (defaults to utcnow).

        Returns:
            Tuple of (can_turn_on, reason).
        """
        if now is None:
            now = dt_util.utcnow()

        if self._last_off_time is None:
            return True, "No previous off time recorded"

        elapsed = now - self._last_off_time
        required = timedelta(minutes=self._min_cycle_off_minutes)

        if elapsed >= required:
            return True, f"Off for {elapsed.total_seconds() / 60:.1f} minutes"

        remaining = required - elapsed
        return False, f"Must wait {remaining.total_seconds() / 60:.1f} more minutes"

    def can_turn_off(self, now: datetime | None = None) -> tuple[bool, str]:
        """Check if the thermostat can be turned off (cycle protection).

        Args:
            now: Current time (defaults to utcnow).

        Returns:
            Tuple of (can_turn_off, reason).
        """
        if now is None:
            now = dt_util.utcnow()

        if self._last_on_time is None:
            return True, "No previous on time recorded"

        elapsed = now - self._last_on_time
        required = timedelta(minutes=self._min_cycle_on_minutes)

        if elapsed >= required:
            return True, f"On for {elapsed.total_seconds() / 60:.1f} minutes"

        remaining = required - elapsed
        return False, f"Must wait {remaining.total_seconds() / 60:.1f} more minutes"

    def record_thermostat_on(self, now: datetime | None = None) -> None:
        """Record that the thermostat was turned on.

        Args:
            now: Current time (defaults to utcnow).
        """
        if now is None:
            now = dt_util.utcnow()
        self._last_on_time = now
        self._current_thermostat_on = True

    def record_thermostat_off(self, now: datetime | None = None) -> None:
        """Record that the thermostat was turned off.

        Args:
            now: Current time (defaults to utcnow).
        """
        if now is None:
            now = dt_util.utcnow()
        self._last_off_time = now
        self._current_thermostat_on = False

    def evaluate_thermostat_action(
        self,
        active_areas: list[AreaOccupancyState],
        area_temp_sensors: dict[str, list[str]],
        inactive_areas: list[AreaOccupancyState] | None = None,
        now: datetime | None = None,
    ) -> ThermostatState:
        """Evaluate what action should be taken with the thermostat.

        This is the main decision-making method that considers:
        - Whether we're paused by contact sensors
        - Current thermostat state and mode
        - Active room temperature satiation
        - Inactive rooms with critical temperature levels
        - Cycle protection timers

        Args:
            active_areas: List of currently active areas from occupancy tracker.
            area_temp_sensors: Dict of area_id -> list of temperature sensor IDs.
            inactive_areas: List of inactive areas to check for critical temps.
            now: Current time (defaults to utcnow).

        Returns:
            ThermostatState with the recommended action.
        """
        if now is None:
            now = dt_util.utcnow()

        if inactive_areas is None:
            inactive_areas = []

        # Get current thermostat state
        hvac_mode, is_on = self.get_thermostat_state()
        target_temp, target_temp_low, target_temp_high = self.get_target_temperatures()

        thermostat_state = ThermostatState(
            thermostat_entity_id=self.thermostat_entity_id,
            hvac_mode=hvac_mode,
            is_on=is_on,
            target_temperature=target_temp,
            target_temp_low=target_temp_low,
            target_temp_high=target_temp_high,
            last_on_time=self._last_on_time,
            last_off_time=self._last_off_time,
        )

        # If paused by contact sensors, no action from us
        if self._is_paused_by_contact_sensors:
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "Paused by open contact sensors"
            return thermostat_state

        # If thermostat is off, check if it was us or the user
        # Track which mode to use for satiation evaluation
        evaluation_hvac_mode = hvac_mode
        if hvac_mode == HVACMode.OFF:
            if self._we_turned_off:
                # We turned it off - don't treat as user choice, continue evaluation
                # to see if we should turn it back on. Use previous mode for satiation.
                _LOGGER.debug("Thermostat is off (we turned it off) - continuing evaluation")
                if self._previous_hvac_mode and self._previous_hvac_mode != HVACMode.OFF.value:
                    try:
                        evaluation_hvac_mode = HVACMode(self._previous_hvac_mode)
                        _LOGGER.debug("Using previous HVAC mode %s for satiation evaluation", evaluation_hvac_mode)
                    except ValueError:
                        # If previous mode is not a valid HVACMode, default to HEAT
                        evaluation_hvac_mode = HVACMode.HEAT
                        _LOGGER.debug("Previous mode invalid, defaulting to HEAT for satiation evaluation")
                else:
                    # No previous mode, default to HEAT
                    evaluation_hvac_mode = HVACMode.HEAT
                    _LOGGER.debug("No previous mode, defaulting to HEAT for satiation evaluation")
            else:
                # User turned it off - respect their choice
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = "Thermostat is off (user choice)"
                return thermostat_state

        # Evaluate each active room for satiation
        thermostat_state.active_room_count = len(active_areas)
        satiated_count = 0

        for area in active_areas:
            temp_sensors = area_temp_sensors.get(area.area_id, [])
            room_state = self.evaluate_room_satiation(
                area,
                temp_sensors,
                evaluation_hvac_mode,
                target_temp,
                target_temp_low,
                target_temp_high,
            )
            room_state.is_active = True
            thermostat_state.room_states[area.area_id] = room_state

            if room_state.is_satiated:
                satiated_count += 1

        thermostat_state.satiated_room_count = satiated_count
        thermostat_state.all_active_rooms_satiated = (
            len(active_areas) > 0 and satiated_count == len(active_areas)
        )

        # Evaluate inactive rooms for critical temperatures
        critical_count = 0
        for area in inactive_areas:
            # Skip if this area was already evaluated as active
            if area.area_id in thermostat_state.room_states:
                continue

            temp_sensors = area_temp_sensors.get(area.area_id, [])
            if not temp_sensors:
                continue  # No sensors, can't evaluate

            room_state = self.evaluate_room_critical(
                area,
                temp_sensors,
                evaluation_hvac_mode,
                target_temp,
                target_temp_low,
                target_temp_high,
            )
            thermostat_state.room_states[area.area_id] = room_state

            if room_state.is_critical:
                critical_count += 1
                _LOGGER.debug(
                    "Inactive room %s is critical: %s",
                    area.area_id,
                    room_state.critical_reason,
                )

        thermostat_state.critical_room_count = critical_count

        # Determine if we need conditioning (active unsatiated OR critical rooms)
        unsatiated_active = len(active_areas) - satiated_count
        needs_conditioning = unsatiated_active > 0 or critical_count > 0

        # Determine recommended action
        if len(active_areas) == 0 and critical_count == 0:
            # No active rooms and no critical rooms
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "No active or critical rooms"
            return thermostat_state

        if not needs_conditioning:
            # All active rooms satiated and no critical rooms - should turn off
            if is_on:
                can_off, reason = self.can_turn_off(now)
                if can_off:
                    thermostat_state.recommended_action = ThermostatAction.TURN_OFF
                    thermostat_state.action_reason = (
                        f"All {satiated_count} active rooms satiated, no critical rooms"
                    )
                else:
                    thermostat_state.recommended_action = ThermostatAction.WAIT_CYCLE_ON
                    thermostat_state.action_reason = f"Want to turn off but {reason}"
            else:
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = "Already off, all rooms satiated"
        else:
            # Some rooms need conditioning (active unsatiated or critical)
            reason_parts = []
            if unsatiated_active > 0:
                reason_parts.append(f"{unsatiated_active} active rooms need conditioning")
            if critical_count > 0:
                reason_parts.append(f"{critical_count} critical rooms")

            if not is_on:
                can_on, cycle_reason = self.can_turn_on(now)
                if can_on:
                    thermostat_state.recommended_action = ThermostatAction.TURN_ON
                    thermostat_state.action_reason = " and ".join(reason_parts)
                else:
                    thermostat_state.recommended_action = ThermostatAction.WAIT_CYCLE_OFF
                    thermostat_state.action_reason = f"Want to turn on but {cycle_reason}"
            else:
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = f"Already on, {' and '.join(reason_parts)}"

        return thermostat_state

    def get_summary(
        self,
        active_areas: list[AreaOccupancyState],
        area_temp_sensors: dict[str, list[str]],
        inactive_areas: list[AreaOccupancyState] | None = None,
    ) -> dict[str, Any]:
        """Get a summary of the current thermostat control state.

        Args:
            active_areas: List of active areas.
            area_temp_sensors: Dict of area_id -> temperature sensor list.
            inactive_areas: List of inactive areas to check for critical temps.

        Returns:
            Dict with summary information.
        """
        state = self.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas
        )

        return {
            "thermostat_entity_id": state.thermostat_entity_id,
            "hvac_mode": state.hvac_mode.value if state.hvac_mode else None,
            "is_on": state.is_on,
            "target_temperature": state.target_temperature,
            "target_temp_low": state.target_temp_low,
            "target_temp_high": state.target_temp_high,
            "temperature_deadband": self._temperature_deadband,
            "is_paused_by_contact_sensors": self._is_paused_by_contact_sensors,
            "we_turned_off": self._we_turned_off,
            "active_room_count": state.active_room_count,
            "satiated_room_count": state.satiated_room_count,
            "critical_room_count": state.critical_room_count,
            "all_active_rooms_satiated": state.all_active_rooms_satiated,
            "recommended_action": state.recommended_action.value,
            "action_reason": state.action_reason,
            "min_cycle_on_minutes": self._min_cycle_on_minutes,
            "min_cycle_off_minutes": self._min_cycle_off_minutes,
            "unoccupied_heating_threshold": self._unoccupied_heating_threshold,
            "unoccupied_cooling_threshold": self._unoccupied_cooling_threshold,
            "rooms": {
                area_id: {
                    "area_name": room.area_name,
                    "is_active": room.is_active,
                    "is_satiated": room.is_satiated,
                    "is_critical": room.is_critical,
                    "satiation_reason": room.satiation_reason.value,
                    "critical_reason": room.critical_reason,
                    "determining_sensor": room.determining_sensor,
                    "determining_temperature": room.determining_temperature,
                    "sensor_readings": room.sensor_readings,
                }
                for area_id, room in state.room_states.items()
            },
        }

    async def async_execute_action(
        self,
        thermostat_state: ThermostatState,
    ) -> bool:
        """Execute the recommended thermostat action.

        Args:
            thermostat_state: The evaluated thermostat state with recommended_action.

        Returns:
            True if an action was executed, False otherwise.
        """
        if thermostat_state.recommended_action == ThermostatAction.NONE:
            return False

        if thermostat_state.recommended_action in (
            ThermostatAction.WAIT_CYCLE_ON,
            ThermostatAction.WAIT_CYCLE_OFF,
        ):
            _LOGGER.debug(
                "Thermostat action %s - waiting for cycle protection: %s",
                thermostat_state.recommended_action.value,
                thermostat_state.action_reason,
            )
            return False

        if thermostat_state.recommended_action == ThermostatAction.TURN_ON:
            # Get the previous HVAC mode to restore
            previous_mode = self._previous_hvac_mode
            if previous_mode and previous_mode != HVACMode.OFF:
                target_mode = previous_mode
            else:
                # Default to heat if no previous mode
                target_mode = HVACMode.HEAT

            _LOGGER.info(
                "Executing thermostat TURN_ON action: setting %s to %s. Reason: %s",
                self.thermostat_entity_id,
                target_mode.value if hasattr(target_mode, 'value') else target_mode,
                thermostat_state.action_reason,
            )

            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    "entity_id": self.thermostat_entity_id,
                    "hvac_mode": target_mode.value if hasattr(target_mode, 'value') else target_mode,
                },
                blocking=True,
            )

            # Update cycle tracking and clear our turn-off flag
            self._last_turn_on_time = dt_util.utcnow()
            self._we_turned_off = False
            return True

        if thermostat_state.recommended_action == ThermostatAction.TURN_OFF:
            _LOGGER.info(
                "Executing thermostat TURN_OFF action: setting %s to off. Reason: %s",
                self.thermostat_entity_id,
                thermostat_state.action_reason,
            )

            # Store current mode before turning off
            current_state = self.hass.states.get(self.thermostat_entity_id)
            if current_state and current_state.state not in (
                STATE_UNAVAILABLE,
                STATE_UNKNOWN,
                HVACMode.OFF.value,
                "off",
            ):
                self._previous_hvac_mode = current_state.state

            await self.hass.services.async_call(
                "climate",
                "set_hvac_mode",
                {
                    "entity_id": self.thermostat_entity_id,
                    "hvac_mode": HVACMode.OFF.value,
                },
                blocking=True,
            )

            # Update cycle tracking and set our turn-off flag
            self._last_turn_off_time = dt_util.utcnow()
            self._we_turned_off = True
            return True

        return False

    async def async_setup(self) -> None:
        """Set up the thermostat controller and restore state from storage."""
        await self._async_restore_state()

    async def async_shutdown(self) -> None:
        """Shut down the thermostat controller and save state."""
        await self._async_save_state()

    async def _async_save_state(self) -> None:
        """Save thermostat controller state to storage."""
        if self._store is None:
            return

        state_data = {
            "we_turned_off": self._we_turned_off,
            "saved_at": dt_util.utcnow().isoformat(),
        }

        await self._store.async_save(state_data)
        _LOGGER.debug("Saved thermostat controller state: we_turned_off=%s", self._we_turned_off)

    async def _async_restore_state(self) -> None:
        """Restore thermostat controller state from storage."""
        if self._store is None:
            return

        stored_data = await self._store.async_load()
        if stored_data is None:
            _LOGGER.debug("No stored thermostat controller state found")
            return

        if stored_data.get("we_turned_off"):
            self._we_turned_off = True
            _LOGGER.debug(
                "Restored thermostat controller state: we_turned_off=True (saved at %s)",
                stored_data.get("saved_at", "unknown"),
            )
