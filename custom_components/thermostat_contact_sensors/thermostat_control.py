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
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MIN_CYCLE_OFF_MINUTES,
    CONF_MIN_CYCLE_ON_MINUTES,
    CONF_TEMPERATURE_DEADBAND,
    DEFAULT_MIN_CYCLE_OFF_MINUTES,
    DEFAULT_MIN_CYCLE_ON_MINUTES,
    DEFAULT_TEMPERATURE_DEADBAND,
)
from .occupancy import AreaOccupancyState, RoomOccupancyTracker

_LOGGER = logging.getLogger(__name__)

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

    # Satiation state
    is_satiated: bool = False
    satiation_reason: SatiationReason = SatiationReason.NO_TEMP_SENSORS

    # The sensor that determined satiation (closest to target)
    determining_sensor: str | None = None
    determining_temperature: float | None = None

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

    # Room states
    room_states: dict[str, RoomTemperatureState] = field(default_factory=dict)

    # Overall state
    all_active_rooms_satiated: bool = False
    active_room_count: int = 0
    satiated_room_count: int = 0

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
        temperature_deadband: float = DEFAULT_TEMPERATURE_DEADBAND,
        min_cycle_on_minutes: int = DEFAULT_MIN_CYCLE_ON_MINUTES,
        min_cycle_off_minutes: int = DEFAULT_MIN_CYCLE_OFF_MINUTES,
    ) -> None:
        """Initialize the thermostat controller.

        Args:
            hass: The Home Assistant instance.
            thermostat_entity_id: Entity ID of the thermostat to control.
            occupancy_tracker: RoomOccupancyTracker instance for occupancy data.
            temperature_deadband: Temperature buffer to prevent cycling.
            min_cycle_on_minutes: Minimum time thermostat must stay on.
            min_cycle_off_minutes: Minimum time thermostat must stay off.
        """
        self.hass = hass
        self.thermostat_entity_id = thermostat_entity_id
        self.occupancy_tracker = occupancy_tracker

        self._temperature_deadband = temperature_deadband
        self._min_cycle_on_minutes = min_cycle_on_minutes
        self._min_cycle_off_minutes = min_cycle_off_minutes

        # State tracking
        self._is_paused_by_contact_sensors = False
        self._last_on_time: datetime | None = None
        self._last_off_time: datetime | None = None
        self._current_thermostat_on: bool = False

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
            room_state.satiation_reason = (
                SatiationReason.SATIATED if is_sat else SatiationReason.NOT_SATIATED
            )

        else:
            # For other modes (OFF, FAN_ONLY, etc.), consider satiated
            room_state.is_satiated = True
            room_state.satiation_reason = SatiationReason.SATIATED

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
        now: datetime | None = None,
    ) -> ThermostatState:
        """Evaluate what action should be taken with the thermostat.

        This is the main decision-making method that considers:
        - Whether we're paused by contact sensors
        - Current thermostat state and mode
        - Active room temperature satiation
        - Cycle protection timers

        Args:
            active_areas: List of currently active areas from occupancy tracker.
            area_temp_sensors: Dict of area_id -> list of temperature sensor IDs.
            now: Current time (defaults to utcnow).

        Returns:
            ThermostatState with the recommended action.
        """
        if now is None:
            now = dt_util.utcnow()

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

        # If thermostat is off (by user choice), don't interfere
        if hvac_mode == HVACMode.OFF:
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "Thermostat is off (user choice)"
            return thermostat_state

        # Evaluate each active room
        thermostat_state.active_room_count = len(active_areas)
        satiated_count = 0

        for area in active_areas:
            temp_sensors = area_temp_sensors.get(area.area_id, [])
            room_state = self.evaluate_room_satiation(
                area,
                temp_sensors,
                hvac_mode,
                target_temp,
                target_temp_low,
                target_temp_high,
            )
            thermostat_state.room_states[area.area_id] = room_state

            if room_state.is_satiated:
                satiated_count += 1

        thermostat_state.satiated_room_count = satiated_count
        thermostat_state.all_active_rooms_satiated = (
            len(active_areas) > 0 and satiated_count == len(active_areas)
        )

        # Determine recommended action
        if len(active_areas) == 0:
            # No active rooms - this case is handled later (per user request)
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "No active rooms"
            return thermostat_state

        if thermostat_state.all_active_rooms_satiated:
            # All rooms satiated - should turn off
            if is_on:
                can_off, reason = self.can_turn_off(now)
                if can_off:
                    thermostat_state.recommended_action = ThermostatAction.TURN_OFF
                    thermostat_state.action_reason = (
                        f"All {satiated_count} active rooms satiated"
                    )
                else:
                    thermostat_state.recommended_action = ThermostatAction.WAIT_CYCLE_ON
                    thermostat_state.action_reason = f"Want to turn off but {reason}"
            else:
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = "Already off, all rooms satiated"
        else:
            # Some rooms not satiated - should turn on
            unsatiated = len(active_areas) - satiated_count
            if not is_on:
                can_on, reason = self.can_turn_on(now)
                if can_on:
                    thermostat_state.recommended_action = ThermostatAction.TURN_ON
                    thermostat_state.action_reason = (
                        f"{unsatiated} of {len(active_areas)} rooms need conditioning"
                    )
                else:
                    thermostat_state.recommended_action = ThermostatAction.WAIT_CYCLE_OFF
                    thermostat_state.action_reason = f"Want to turn on but {reason}"
            else:
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = (
                    f"Already on, {unsatiated} rooms need conditioning"
                )

        return thermostat_state

    def get_summary(
        self, active_areas: list[AreaOccupancyState], area_temp_sensors: dict[str, list[str]]
    ) -> dict[str, Any]:
        """Get a summary of the current thermostat control state.

        Args:
            active_areas: List of active areas.
            area_temp_sensors: Dict of area_id -> temperature sensor list.

        Returns:
            Dict with summary information.
        """
        state = self.evaluate_thermostat_action(active_areas, area_temp_sensors)

        return {
            "thermostat_entity_id": state.thermostat_entity_id,
            "hvac_mode": state.hvac_mode.value if state.hvac_mode else None,
            "is_on": state.is_on,
            "target_temperature": state.target_temperature,
            "target_temp_low": state.target_temp_low,
            "target_temp_high": state.target_temp_high,
            "temperature_deadband": self._temperature_deadband,
            "is_paused_by_contact_sensors": self._is_paused_by_contact_sensors,
            "active_room_count": state.active_room_count,
            "satiated_room_count": state.satiated_room_count,
            "all_active_rooms_satiated": state.all_active_rooms_satiated,
            "recommended_action": state.recommended_action.value,
            "action_reason": state.action_reason,
            "min_cycle_on_minutes": self._min_cycle_on_minutes,
            "min_cycle_off_minutes": self._min_cycle_off_minutes,
            "rooms": {
                area_id: {
                    "area_name": room.area_name,
                    "is_satiated": room.is_satiated,
                    "satiation_reason": room.satiation_reason.value,
                    "determining_sensor": room.determining_sensor,
                    "determining_temperature": room.determining_temperature,
                    "sensor_readings": room.sensor_readings,
                }
                for area_id, room in state.room_states.items()
            },
        }
