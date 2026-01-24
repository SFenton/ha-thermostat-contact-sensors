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

from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.const import (
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_COOLING_BOOST_OFFSET,
    DEFAULT_HEATING_BOOST_OFFSET,
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
ATTR_FAN_MODE = "fan_mode"
ATTR_FAN_MODES = "fan_modes"

# Common fan mode values
FAN_MODE_ON = "on"
FAN_MODE_AUTO = "auto"
FAN_MODE_OFF = "off"


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

    # Inferred HVAC mode (when thermostat is off, based on global temp trend)
    inferred_hvac_mode: HVACMode | None = None

    # What unsatiated/critical rooms need (for consensus logic)
    rooms_need_heat: bool = False
    rooms_need_cool: bool = False

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


def infer_effective_hvac_mode(
    all_sensor_readings: dict[str, float],
    target_temp_low: float | None,
    target_temp_high: float | None,
) -> HVACMode | None:
    """Infer whether we're closer to needing heat or cooling.

    When HVAC is off (idle), we look at all temperature sensors and determine
    whether on average we're closer to needing heating or cooling. This is used
    for intelligent satiation evaluation and mode selection during shoulder
    seasons (spring/fall) when HVAC may bounce between modes.

    Args:
        all_sensor_readings: Dict of sensor_id -> temperature for ALL sensors.
        target_temp_low: The heating target temperature.
        target_temp_high: The cooling target temperature.

    Returns:
        HVACMode.HEAT if we're closer to needing heat,
        HVACMode.COOL if we're closer to needing cooling,
        None if we can't determine (no readings or no targets).
    """
    if target_temp_low is None or target_temp_high is None:
        return None

    if not all_sensor_readings:
        return None

    # Calculate average temperature across all sensors
    all_temps = list(all_sensor_readings.values())
    avg_temp = sum(all_temps) / len(all_temps)

    # Calculate distance to each target
    # Positive distance_to_heat means we're below heating target (need heat)
    # Positive distance_to_cool means we're above cooling target (need cool)
    distance_to_heat = target_temp_low - avg_temp  # Positive if cold
    distance_to_cool = avg_temp - target_temp_high  # Positive if hot

    _LOGGER.debug(
        "Infer HVAC mode: avg_temp=%.2f, target_low=%.2f, target_high=%.2f, "
        "distance_to_heat=%.2f, distance_to_cool=%.2f",
        avg_temp,
        target_temp_low,
        target_temp_high,
        distance_to_heat,
        distance_to_cool,
    )

    # If we're in the comfort zone (between targets), use whichever
    # boundary we're closer to
    if distance_to_heat <= 0 and distance_to_cool <= 0:
        # We're within the comfort band - compare absolute distances to boundaries
        if abs(distance_to_heat) < abs(distance_to_cool):
            # Closer to heating threshold
            return HVACMode.HEAT
        else:
            # Closer to cooling threshold
            return HVACMode.COOL
    elif distance_to_heat > 0:
        # We're below heating target - need heat
        return HVACMode.HEAT
    else:
        # We're above cooling target - need cool
        return HVACMode.COOL


def determine_rooms_need_mode(
    room_states: dict[str, "RoomTemperatureState"],
    target_temp_low: float,
    target_temp_high: float,
    deadband: float,
    heating_critical_offset: float = 3.0,
    cooling_critical_offset: float = 3.0,
) -> tuple[bool, bool]:
    """Determine what conditioning mode rooms need based on absolute temperature.

    This function checks rooms to determine what mode they need based on 
    absolute temperature vs thresholds. This is used for consensus logic to 
    detect anomalies like "house is warm but the active room is cold".

    The key insight is that satiation status is mode-dependent (a cold room is
    "satiated" in COOL mode), but for consensus we need mode-independent
    analysis of what rooms truly need.

    Considers:
    - Active rooms: Check if they need conditioning (temp vs comfort thresholds)
    - Inactive rooms: Check if they're in critical temp range (extreme temps)

    The is_critical flag on room_state is mode-dependent (set during room 
    evaluation for a specific mode), so we re-evaluate critical thresholds
    here in a mode-independent way.

    Args:
        room_states: Dict of area_id -> RoomTemperatureState.
        target_temp_low: Heating target temperature.
        target_temp_high: Cooling target temperature.
        deadband: Temperature deadband.
        heating_critical_offset: Degrees below target for critical heating.
        cooling_critical_offset: Degrees above target for critical cooling.

    Returns:
        Tuple of (any_need_heat, any_need_cool).
    """
    need_heat = False
    need_cool = False

    # Comfort thresholds for active rooms
    heat_threshold = target_temp_low - deadband
    cool_threshold = target_temp_high + deadband

    # Critical thresholds for inactive rooms (mode-independent)
    heat_critical_threshold = target_temp_low - heating_critical_offset
    cool_critical_threshold = target_temp_high + cooling_critical_offset

    for room_state in room_states.values():
        if room_state.determining_temperature is None:
            continue

        temp = room_state.determining_temperature

        # For active rooms, check against comfort thresholds
        if room_state.is_active:
            if temp < heat_threshold:
                need_heat = True
            elif temp > cool_threshold:
                need_cool = True
        # For inactive rooms, check against CRITICAL thresholds (mode-independent)
        # This catches rooms like a cold basement even when we inferred COOL mode
        else:
            if temp < heat_critical_threshold:
                need_heat = True
            elif temp > cool_critical_threshold:
                need_cool = True

    return need_heat, need_cool


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
        heating_boost_offset: float = DEFAULT_HEATING_BOOST_OFFSET,
        cooling_boost_offset: float = DEFAULT_COOLING_BOOST_OFFSET,
        area_thermostats_getter: callable | None = None,
        global_thermostat_getter: callable | None = None,
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
            heating_boost_offset: Degrees to add to the heat setpoint when turning on.
                This ensures the physical thermostat calls for heat.
            cooling_boost_offset: Degrees to subtract from cool setpoint when turning on.
                This ensures the physical thermostat calls for cooling.
            area_thermostats_getter: Callback to get dict of area_id -> AreaVirtualThermostat.
            global_thermostat_getter: Callback to get the GlobalVirtualThermostat.
        """
        self.hass = hass
        self.thermostat_entity_id = thermostat_entity_id
        self.occupancy_tracker = occupancy_tracker
        self._area_thermostats_getter = area_thermostats_getter
        self._global_thermostat_getter = global_thermostat_getter

        self._temperature_deadband = temperature_deadband
        self._min_cycle_on_minutes = min_cycle_on_minutes
        self._min_cycle_off_minutes = min_cycle_off_minutes
        self._unoccupied_heating_threshold = unoccupied_heating_threshold
        self._unoccupied_cooling_threshold = unoccupied_cooling_threshold
        self._heating_boost_offset = heating_boost_offset
        self._cooling_boost_offset = cooling_boost_offset

        # State tracking
        self._is_paused_by_contact_sensors = False
        self._last_on_time: datetime | None = None
        self._last_off_time: datetime | None = None
        self._current_thermostat_on: bool = False
        self._we_turned_off: bool = False  # Track if integration turned off thermostat
        self._previous_hvac_mode: str | None = None  # Track mode before we turned off

        # Fan mode tracking
        self._previous_fan_mode: str | None = None  # Track fan mode before we changed it
        self._we_changed_fan_mode: bool = False  # Track if we changed fan mode

        # Stored target temperatures (captured when thermostat is ON)
        self._stored_target_temp: float | None = None
        self._stored_target_temp_low: float | None = None
        self._stored_target_temp_high: float | None = None

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
    def heating_boost_offset(self) -> float:
        """Return the heating boost offset."""
        return self._heating_boost_offset

    @heating_boost_offset.setter
    def heating_boost_offset(self, value: float) -> None:
        """Set the heating boost offset."""
        self._heating_boost_offset = value

    @property
    def cooling_boost_offset(self) -> float:
        """Return the cooling boost offset."""
        return self._cooling_boost_offset

    @cooling_boost_offset.setter
    def cooling_boost_offset(self, value: float) -> None:
        """Set the cooling boost offset."""
        self._cooling_boost_offset = value

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

    def supports_fan_mode(self) -> bool:
        """Check if the thermostat supports fan mode control.

        Returns:
            True if the thermostat supports fan mode.
        """
        state = self.hass.states.get(self.thermostat_entity_id)
        if state is None:
            return False

        supported_features = state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)
        return bool(supported_features & ClimateEntityFeature.FAN_MODE)

    def get_fan_mode(self) -> str | None:
        """Get the current fan mode.

        Returns:
            The current fan mode string, or None if unavailable.
        """
        state = self.hass.states.get(self.thermostat_entity_id)
        if state is None:
            return None

        return state.attributes.get(ATTR_FAN_MODE)

    def get_available_fan_modes(self) -> list[str]:
        """Get the list of available fan modes.

        Returns:
            List of available fan mode strings.
        """
        state = self.hass.states.get(self.thermostat_entity_id)
        if state is None:
            return []

        return state.attributes.get(ATTR_FAN_MODES, [])

    def _get_best_fan_on_mode(self) -> str | None:
        """Get the best fan mode to use when turning fan on.

        Prefers 'on' but falls back to other high-airflow modes.

        Returns:
            The fan mode to use, or None if no suitable mode found.
        """
        available = self.get_available_fan_modes()
        if not available:
            return None

        # Preference order for "fan on" modes
        preferred = [FAN_MODE_ON, "high", "medium", "low"]
        for mode in preferred:
            if mode in available:
                return mode

        # Return first available if none of the preferred modes exist
        return available[0] if available else None

    def _get_best_fan_off_mode(self) -> str | None:
        """Get the best fan mode to use when turning fan off/auto.

        Prefers 'auto' but falls back to 'off'.

        Returns:
            The fan mode to use, or None if no suitable mode found.
        """
        available = self.get_available_fan_modes()
        if not available:
            return None

        # Preference order for "fan off/auto" modes
        preferred = [FAN_MODE_AUTO, FAN_MODE_OFF]
        for mode in preferred:
            if mode in available:
                return mode

        return None

    def get_target_temperatures(
        self,
        hvac_mode_override: HVACMode | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        """Get target temperatures from the global virtual thermostat.

        The global virtual thermostat aggregates targets from all area thermostats:
        - Heating target = MAX of all area heating targets
        - Cooling target = MIN of all area cooling targets

        When away mode is active, returns the effective (adjusted) temperatures.

        Falls back to physical thermostat if global virtual thermostat is not available.

        Args:
            hvac_mode_override: If provided, use this HVAC mode instead of the
                current thermostat mode for computing target_temp. This is used
                when the thermostat is OFF but we want to evaluate satiation
                based on the previous/intended mode.

        Returns:
            Tuple of (target_temperature, target_temp_low, target_temp_high).
            - target_temperature: Used for HEAT or COOL mode
            - target_temp_low: Heating setpoint for HEAT_COOL mode
            - target_temp_high: Cooling setpoint for HEAT_COOL mode
        """
        # First, try to get targets from the global virtual thermostat
        if self._global_thermostat_getter:
            global_thermostat = self._global_thermostat_getter()
            if global_thermostat is not None:
                # Use effective temps which include away mode adjustment
                target_temp_low = global_thermostat.effective_target_temp_low
                target_temp_high = global_thermostat.effective_target_temp_high

                # For HEAT/COOL modes, use low/high respectively
                # Use override if provided, otherwise check current HVAC mode
                if hvac_mode_override is not None:
                    hvac_mode = hvac_mode_override
                else:
                    hvac_mode, _ = self.get_thermostat_state()
                
                if hvac_mode == HVACMode.HEAT:
                    target_temp = target_temp_low
                elif hvac_mode == HVACMode.COOL:
                    target_temp = target_temp_high
                else:
                    # For HEAT_COOL or other modes, use midpoint
                    target_temp = (target_temp_low + target_temp_high) / 2 if target_temp_low and target_temp_high else None

                _LOGGER.debug(
                    "Using global virtual thermostat targets: temp=%s, low=%s, high=%s (mode=%s)",
                    target_temp,
                    target_temp_low,
                    target_temp_high,
                    hvac_mode,
                )
                return target_temp, target_temp_low, target_temp_high

        # Fall back to physical thermostat
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

        # If we have valid values, store them for when thermostat is OFF
        # Also persist to storage whenever values change
        values_changed = False
        if target_temp is not None:
            if self._stored_target_temp != target_temp:
                values_changed = True
            self._stored_target_temp = target_temp
        if target_temp_low is not None:
            if self._stored_target_temp_low != target_temp_low:
                values_changed = True
            self._stored_target_temp_low = target_temp_low
        if target_temp_high is not None:
            if self._stored_target_temp_high != target_temp_high:
                values_changed = True
            self._stored_target_temp_high = target_temp_high

        # Persist to storage if values changed
        if values_changed and self._store:
            self.hass.async_create_task(self._async_save_state())

        # Always use stored values as fallback when thermostat values are unavailable
        # This handles: thermostat OFF, reboot when OFF, or mode without certain targets
        final_target_temp = target_temp if target_temp is not None else self._stored_target_temp
        final_target_temp_low = target_temp_low if target_temp_low is not None else self._stored_target_temp_low
        final_target_temp_high = target_temp_high if target_temp_high is not None else self._stored_target_temp_high

        # When hvac_mode_override is provided, use it to select the correct target_temp
        # This is critical when thermostat is OFF but we've inferred HEAT or COOL mode
        if hvac_mode_override is not None:
            if hvac_mode_override == HVACMode.HEAT and final_target_temp_low is not None:
                final_target_temp = final_target_temp_low
            elif hvac_mode_override == HVACMode.COOL and final_target_temp_high is not None:
                final_target_temp = final_target_temp_high
            elif hvac_mode_override == HVACMode.HEAT_COOL and final_target_temp_low and final_target_temp_high:
                final_target_temp = (final_target_temp_low + final_target_temp_high) / 2

        hvac_mode, _ = self.get_thermostat_state()
        if hvac_mode == HVACMode.OFF or target_temp is None or target_temp_low is None or target_temp_high is None:
            _LOGGER.debug(
                "Using stored/merged target temps: temp=%s, low=%s, high=%s (override=%s)",
                final_target_temp,
                final_target_temp_low,
                final_target_temp_high,
                hvac_mode_override,
            )

        return final_target_temp, final_target_temp_low, final_target_temp_high

    def get_area_target_temperatures(
        self,
        area_id: str,
        hvac_mode_override: HVACMode | None = None,
    ) -> tuple[float | None, float | None, float | None]:
        """Get target temperatures for a specific area.

        This gets the targets from the area's virtual thermostat if available,
        falling back to the physical thermostat's targets if not.

        Each room uses its own virtual thermostat's heat/cool targets for
        satiation and critical temperature evaluation.

        Args:
            area_id: The area to get targets for.
            hvac_mode_override: If provided, use this HVAC mode instead of the
                current thermostat mode for computing target_temp. This is used
                when the thermostat is OFF but we want to evaluate satiation
                based on the previous/intended mode.

        Returns:
            Tuple of (target_temperature, target_temp_low, target_temp_high).
            - target_temperature: Used for HEAT or COOL mode (uses low for heat, high for cool)
            - target_temp_low: Heating setpoint for HEAT_COOL mode
            - target_temp_high: Cooling setpoint for HEAT_COOL mode
        """
        # Try to get targets from the area's virtual thermostat
        if self._area_thermostats_getter:
            area_thermostats = self._area_thermostats_getter()
            if area_thermostats and area_id in area_thermostats:
                area_thermostat = area_thermostats[area_id]
                # Use effective temps which include away mode adjustment
                target_temp_low = area_thermostat.effective_target_temp_low
                target_temp_high = area_thermostat.effective_target_temp_high
                
                # For HEAT mode, target_temp should be target_temp_low
                # For COOL mode, target_temp should be target_temp_high
                # Use override if provided, otherwise check current HVAC mode
                if hvac_mode_override is not None:
                    hvac_mode = hvac_mode_override
                else:
                    hvac_mode, _ = self.get_thermostat_state()
                
                if hvac_mode == HVACMode.HEAT:
                    target_temp = target_temp_low
                elif hvac_mode == HVACMode.COOL:
                    target_temp = target_temp_high
                else:
                    # For HEAT_COOL or other modes, use average (though low/high will be used directly)
                    target_temp = (target_temp_low + target_temp_high) / 2 if target_temp_low and target_temp_high else None
                
                _LOGGER.debug(
                    "Using area %s virtual thermostat targets: low=%s, high=%s, temp=%s (mode=%s)",
                    area_id,
                    target_temp_low,
                    target_temp_high,
                    target_temp,
                    hvac_mode,
                )
                return target_temp, target_temp_low, target_temp_high

        # Fall back to physical thermostat targets
        _LOGGER.debug(
            "No virtual thermostat for area %s, using physical thermostat targets",
            area_id,
        )
        return self.get_target_temperatures(hvac_mode_override=hvac_mode_override)

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

        # Helper: compute average temperature when we can't determine satiation
        def set_average_temperature() -> None:
            """Set determining temperature to average of all readings when no target."""
            readings = list(room_state.sensor_readings.values())
            avg_temp = sum(readings) / len(readings)
            # Use the sensor closest to the average as the "determining" sensor
            closest_sensor = min(
                room_state.sensor_readings.keys(),
                key=lambda s: abs(room_state.sensor_readings[s] - avg_temp)
            )
            room_state.determining_sensor = closest_sensor
            room_state.determining_temperature = avg_temp

        # Evaluate satiation based on HVAC mode
        if hvac_mode == HVACMode.HEAT:
            if target_temp is None:
                room_state.satiation_reason = SatiationReason.NO_TARGET_TEMP
                set_average_temperature()
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
                set_average_temperature()
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
                set_average_temperature()
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
            # For other modes (OFF, FAN_ONLY, etc.), use average temp and consider satiated
            set_average_temperature()
            room_state.is_satiated = True
            room_state.satiation_reason = SatiationReason.SATIATED

        # Evaluate critical status for active rooms as well
        # Active rooms can be both active and critical (e.g., occupied but too cold/hot)
        if hvac_mode == HVACMode.HEAT and target_temp is not None:
            coldest_sensor, coldest_temp = min(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            critical_threshold = target_temp - self._unoccupied_heating_threshold
            if coldest_temp < critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {coldest_temp:.1f}° is {target_temp - coldest_temp:.1f}° "
                    f"below heat target {target_temp:.1f}° (threshold: {self._unoccupied_heating_threshold:.1f}°)"
                )
        elif hvac_mode == HVACMode.COOL and target_temp is not None:
            warmest_sensor, warmest_temp = max(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            critical_threshold = target_temp + self._unoccupied_cooling_threshold
            if warmest_temp > critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {warmest_temp:.1f}° is {warmest_temp - target_temp:.1f}° "
                    f"above cool target {target_temp:.1f}° (threshold: {self._unoccupied_cooling_threshold:.1f}°)"
                )
        elif hvac_mode == HVACMode.HEAT_COOL and target_temp_low is not None and target_temp_high is not None:
            coldest_sensor, coldest_temp = min(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            warmest_sensor, warmest_temp = max(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            heat_critical_threshold = target_temp_low - self._unoccupied_heating_threshold
            cool_critical_threshold = target_temp_high + self._unoccupied_cooling_threshold
            
            if coldest_temp < heat_critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {coldest_temp:.1f}° is {target_temp_low - coldest_temp:.1f}° "
                    f"below heat target {target_temp_low:.1f}° (threshold: {self._unoccupied_heating_threshold:.1f}°)"
                )
            elif warmest_temp > cool_critical_threshold:
                room_state.is_critical = True
                room_state.critical_reason = (
                    f"Temperature {warmest_temp:.1f}° is {warmest_temp - target_temp_high:.1f}° "
                    f"above cool target {target_temp_high:.1f}° (threshold: {self._unoccupied_cooling_threshold:.1f}°)"
                )

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
            if temperature_sensors:
                room_state.satiation_reason = SatiationReason.ALL_SENSORS_UNAVAILABLE
            else:
                room_state.satiation_reason = SatiationReason.NO_TEMP_SENSORS
            return room_state

        # For inactive rooms, we don't evaluate satiation (they're not participating
        # in the "is the room comfortable" decision), but we still need to set a
        # meaningful satiation_reason to indicate we have valid sensor data
        room_state.satiation_reason = SatiationReason.NOT_SATIATED

        # Helper: compute average temperature when we can't determine critical state
        def set_average_temperature() -> None:
            """Set determining temperature to average of all readings when no target."""
            readings = list(room_state.sensor_readings.values())
            avg_temp = sum(readings) / len(readings)
            # Use the sensor closest to the average as the "determining" sensor
            closest_sensor = min(
                room_state.sensor_readings.keys(),
                key=lambda s: abs(room_state.sensor_readings[s] - avg_temp)
            )
            room_state.determining_sensor = closest_sensor
            room_state.determining_temperature = avg_temp

        # Use most favorable sensor (closest to target) - only critical if whole room is in trouble
        if hvac_mode == HVACMode.HEAT:
            if target_temp is None:
                set_average_temperature()
                return room_state

            # For heating critical protection, use the warmest sensor (same as satiation logic)
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
                set_average_temperature()
                return room_state

            # For cooling critical protection, use the coolest sensor (same as satiation logic)
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
                set_average_temperature()
                return room_state

            # Use worst-case sensors for critical protection
            coldest_sensor, coldest_temp = min(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )
            warmest_sensor, warmest_temp = max(
                room_state.sensor_readings.items(), key=lambda x: x[1]
            )

            heat_critical_threshold = target_temp_low - self._unoccupied_heating_threshold
            cool_critical_threshold = target_temp_high + self._unoccupied_cooling_threshold

            # For heating critical: any spot is too cold (use coldest)
            if coldest_temp < heat_critical_threshold:
                room_state.is_critical = True
                room_state.determining_sensor = coldest_sensor
                room_state.determining_temperature = coldest_temp
                room_state.critical_reason = (
                    f"Temperature {coldest_temp:.1f}° is {target_temp_low - coldest_temp:.1f}° "
                    f"below heat target {target_temp_low:.1f}° (threshold: {self._unoccupied_heating_threshold:.1f}°)"
                )
            # For cooling critical: any spot is too hot (use warmest)
            elif warmest_temp > cool_critical_threshold:
                room_state.is_critical = True
                room_state.determining_sensor = warmest_sensor
                room_state.determining_temperature = warmest_temp
                room_state.critical_reason = (
                    f"Temperature {warmest_temp:.1f}° is {warmest_temp - target_temp_high:.1f}° "
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
        respect_user_off: bool = True,
        eco_mode: bool = False,
        eco_away_targets: tuple[float, float] | None = None,
        all_areas_for_trend: list[AreaOccupancyState] | None = None,
        tracked_area_ids: set[str] | None = None,
        force_critical_area_ids: set[str] | None = None,
    ) -> ThermostatState:
        """Evaluate what action should be taken with the thermostat.

        This is the main decision-making method that considers:
        - Whether we're paused by contact sensors
        - Current thermostat state and mode
        - Active room temperature satiation
        - Inactive rooms with critical temperature levels (critical protection)
        - Cycle protection timers
        - Whether to respect user's choice to turn thermostat off

        Args:
            active_areas: List of currently active areas from occupancy tracker.
            area_temp_sensors: Dict of area_id -> list of temperature sensor IDs.
            inactive_areas: List of inactive areas to check for critical temps.
            now: Current time (defaults to utcnow).
            respect_user_off: If True (default), when the user turns off the
                thermostat, the integration won't turn it back on. If False,
                the integration will turn it on when rooms need conditioning.
            eco_mode: If True, only consider active (occupied) rooms for *normal*
                thermostat control decisions. Critical temperature protection can
                still trigger HVAC operation.
            eco_away_targets: Optional tuple of (heat_target, cool_target) to use
                when eco mode is active and the user is away with "use_eco_away_targets"
                behavior. If provided, these targets will be used instead of area targets.
            all_areas_for_trend: Optional list of ALL areas (regardless of tracking filter)
                to use for global temperature trend calculation (anomaly detection).
                If not provided, uses active_areas + inactive_areas.
            tracked_area_ids: Optional set of area IDs that are being tracked for
                heating/cooling decisions. If provided, only these areas will count
                toward satiation/critical room decisions. All areas are still evaluated
                for temperature display. If None, all areas are considered tracked.
            force_critical_area_ids: Optional set of area IDs that should be considered
                for CRITICAL temperature protection even when they are not tracked.
                This is intended to be used with the tracked rooms feature.

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

        # Track if paused - we still evaluate rooms for display, but won't take actions
        is_paused = self._is_paused_by_contact_sensors

        # Helper to check if an area is tracked for decision-making
        def is_area_tracked(area_id: str) -> bool:
            if tracked_area_ids is None:
                return True  # All areas tracked when not specified
            return area_id in tracked_area_ids

        # Helper: should this area be considered for CRITICAL protection decisions?
        def is_area_critical_eligible(area_id: str) -> bool:
            if is_area_tracked(area_id):
                return True
            if force_critical_area_ids is None:
                return False
            return area_id in force_critical_area_ids

        # Collect ALL sensor readings first to calculate global temperature trend
        # This is used to infer whether we're closer to needing heat or cooling
        # Use all_areas_for_trend if provided (for tracked rooms feature to still detect anomalies)
        # Otherwise, fall back to active + inactive areas
        all_sensor_readings: dict[str, float] = {}
        areas_for_trend = all_areas_for_trend if all_areas_for_trend is not None else list(active_areas) + list(inactive_areas)
        for area in areas_for_trend:
            temp_sensors = area_temp_sensors.get(area.area_id, [])
            for sensor_id in temp_sensors:
                state = self.hass.states.get(sensor_id)
                temp = get_temperature_from_state(state)
                if temp is not None:
                    all_sensor_readings[sensor_id] = temp

        # Determine evaluation HVAC mode
        # If thermostat is off, infer the mode from global temperature trend
        evaluation_hvac_mode = hvac_mode
        inferred_mode: HVACMode | None = None

        if hvac_mode == HVACMode.OFF:
            # Infer mode from global temperature trend
            inferred_mode = infer_effective_hvac_mode(
                all_sensor_readings, target_temp_low, target_temp_high
            )
            thermostat_state.inferred_hvac_mode = inferred_mode

            if inferred_mode:
                evaluation_hvac_mode = inferred_mode
                _LOGGER.debug(
                    "Thermostat is off - inferred mode %s from %d sensors (avg: %.2f°F)",
                    inferred_mode.value,
                    len(all_sensor_readings),
                    sum(all_sensor_readings.values()) / len(all_sensor_readings) if all_sensor_readings else 0,
                )
            else:
                # Fallback to HEAT if we can't infer (no sensors or no targets)
                evaluation_hvac_mode = HVACMode.HEAT
                _LOGGER.debug("Could not infer HVAC mode, defaulting to HEAT for satiation evaluation")

            if self._we_turned_off:
                _LOGGER.debug("Thermostat is off (we turned it off) - continuing evaluation")
            elif respect_user_off:
                _LOGGER.debug("Thermostat is off (user choice) - evaluating temps but taking no action")
            else:
                _LOGGER.debug(
                    "Thermostat is off (user turned it off) but respect_user_off is False - "
                    "will evaluate and potentially turn on if rooms need conditioning"
                )

        # Flag if user turned thermostat off AND we should respect that choice
        # When respect_user_off is False, we treat user's off as if we turned it off
        user_turned_off = hvac_mode == HVACMode.OFF and not self._we_turned_off and respect_user_off

        # Helper to get target temperatures, optionally using eco_away_targets
        def get_targets_for_area(area_id: str) -> tuple[float | None, float | None, float | None]:
            if eco_away_targets is not None:
                # Use eco away targets for all areas
                eco_target_low, eco_target_high = eco_away_targets
                if evaluation_hvac_mode == HVACMode.HEAT:
                    eco_target_temp = eco_target_low
                elif evaluation_hvac_mode == HVACMode.COOL:
                    eco_target_temp = eco_target_high
                else:
                    eco_target_temp = (eco_target_low + eco_target_high) / 2
                return eco_target_temp, eco_target_low, eco_target_high
            else:
                # Use normal area targets
                return self.get_area_target_temperatures(area_id, hvac_mode_override=evaluation_hvac_mode)

        # Evaluate each active room for satiation (always, even when OFF for display)
        # Count only tracked rooms for decision-making, but evaluate ALL for display
        tracked_active_count = 0
        satiated_count = 0

        for area in active_areas:
            temp_sensors = area_temp_sensors.get(area.area_id, [])
            # Get area-specific target temperatures from virtual thermostat
            # Pass evaluation_hvac_mode so we get the correct target even when thermostat is OFF
            area_target_temp, area_target_temp_low, area_target_temp_high = (
                get_targets_for_area(area.area_id)
            )
            room_state = self.evaluate_room_satiation(
                area,
                temp_sensors,
                evaluation_hvac_mode,
                area_target_temp,
                area_target_temp_low,
                area_target_temp_high,
            )
            room_state.is_active = True
            thermostat_state.room_states[area.area_id] = room_state

            # Only count tracked rooms for decisions
            if is_area_tracked(area.area_id):
                tracked_active_count += 1
                if room_state.is_satiated:
                    satiated_count += 1

        thermostat_state.active_room_count = tracked_active_count
        thermostat_state.satiated_room_count = satiated_count
        thermostat_state.all_active_rooms_satiated = (
            tracked_active_count > 0 and satiated_count == tracked_active_count
        )

        # Evaluate inactive rooms for critical temperatures
        # We always evaluate for display. For decisions, only count rooms that are
        # either tracked OR force-critical eligible.
        critical_count = 0
        for area in inactive_areas:
            # Skip if this area was already evaluated as active
            if area.area_id in thermostat_state.room_states:
                continue

            temp_sensors = area_temp_sensors.get(area.area_id, [])
            if not temp_sensors:
                continue  # No sensors, can't evaluate

            # Get area-specific target temperatures from virtual thermostat
            # Pass evaluation_hvac_mode so we get the correct target even when thermostat is OFF
            area_target_temp, area_target_temp_low, area_target_temp_high = (
                get_targets_for_area(area.area_id)
            )
            room_state = self.evaluate_room_critical(
                area,
                temp_sensors,
                evaluation_hvac_mode,
                area_target_temp,
                area_target_temp_low,
                area_target_temp_high,
            )
            thermostat_state.room_states[area.area_id] = room_state

            # Only count as critical for thermostat control if the room is eligible
            # (tracked or force-critical). Critical protection overrides eco mode.
            if room_state.is_critical and is_area_critical_eligible(area.area_id):
                critical_count += 1
                _LOGGER.debug(
                    "Inactive room %s is critical: %s",
                    area.area_id,
                    room_state.critical_reason,
                )
            elif room_state.is_critical and not is_area_critical_eligible(area.area_id):
                _LOGGER.debug(
                    "Inactive room %s is critical but not tracked (ignoring for decisions): %s",
                    area.area_id,
                    room_state.critical_reason,
                )

        thermostat_state.critical_room_count = critical_count

        # Determine what the rooms that need conditioning actually need (heat or cool)
        # This uses absolute temperature thresholds, not mode-specific satiation
        # Use target_temp as fallback for low/high if not available (ensures same units)
        effective_target_low = target_temp_low or target_temp or 70.0
        effective_target_high = target_temp_high or target_temp or 78.0
        
        # For mode determination, only consider:
        # 1. Tracked active rooms (normal eco-mode behavior)
        # 2. Any CRITICAL-eligible room that is currently critical
        rooms_for_mode_check = {
            area_id: room_state
            for area_id, room_state in thermostat_state.room_states.items()
            if (
                (is_area_tracked(area_id) and (room_state.is_active or not eco_mode))
                or (room_state.is_critical and is_area_critical_eligible(area_id))
            )
        }
        rooms_need_heat, rooms_need_cool = determine_rooms_need_mode(
            rooms_for_mode_check,
            effective_target_low,
            effective_target_high,
            self._temperature_deadband,
            self._unoccupied_heating_threshold,
            self._unoccupied_cooling_threshold,
        )
        thermostat_state.rooms_need_heat = rooms_need_heat
        thermostat_state.rooms_need_cool = rooms_need_cool

        # Determine if we need conditioning
        # When HVAC is OFF and we've inferred a mode, use absolute temperature needs
        # (not mode-specific satiation which can be misleading)
        # In eco mode, only consider active rooms (critical_count will be 0)
        # Use tracked_active_count (only tracked rooms) for decision making
        unsatiated_active = tracked_active_count - satiated_count
        if hvac_mode == HVACMode.OFF:
            # Use absolute temperature needs for consensus logic
            needs_conditioning = rooms_need_heat or rooms_need_cool or critical_count > 0
        else:
            # HVAC is on - use normal satiation logic
            needs_conditioning = unsatiated_active > 0 or critical_count > 0

        _LOGGER.debug(
            "Rooms need conditioning: heat=%s, cool=%s, inferred_mode=%s",
            rooms_need_heat,
            rooms_need_cool,
            inferred_mode.value if inferred_mode else "N/A",
        )
        # Check if any rooms are configured at all
        # (if no active AND no inactive areas, no rooms are configured)
        rooms_configured = len(active_areas) > 0 or len(inactive_areas) > 0

        # If user turned thermostat off, don't recommend any action
        # (but we've still evaluated room temps above for display purposes)
        if user_turned_off:
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "Thermostat is off (user choice)"
            return thermostat_state

        # If paused by contact sensors, don't recommend any action
        # (but we've still evaluated room temps above for display purposes)
        if is_paused:
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "Paused by open contact sensors"
            return thermostat_state

        # Determine recommended action
        # Note: critical_count is mode-dependent (based on inferred mode evaluation)
        # but rooms_need_heat/rooms_need_cool are mode-independent
        # A room can need heat even if critical_count=0 (e.g., cold basement when trend=COOL)
        has_mode_independent_critical = rooms_need_heat or rooms_need_cool
        
        if len(active_areas) == 0 and critical_count == 0 and not has_mode_independent_critical:
            # No active rooms and no critical rooms (either mode-dependent or mode-independent)
            if not rooms_configured:
                # No rooms configured at all - don't control thermostat
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = "No rooms configured"
                return thermostat_state

            # Rooms are configured but none are currently active/critical.
            # In this state, we avoid actively turning the thermostat off; we simply
            # do nothing and wait for occupancy or critical conditions.
            thermostat_state.recommended_action = ThermostatAction.NONE
            thermostat_state.action_reason = "No active or critical rooms (idle)"
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
            # Add mode-independent critical needs (e.g., cold basement when trend=COOL)
            if rooms_need_heat and critical_count == 0 and unsatiated_active == 0:
                reason_parts.append("inactive room(s) in critical heat range")
            if rooms_need_cool and critical_count == 0 and unsatiated_active == 0:
                reason_parts.append("inactive room(s) in critical cool range")

            if not is_on:
                # Apply consensus logic: only turn on if the inferred mode aligns
                # with what the rooms need
                mode_to_engage: HVACMode | None = None
                consensus_reason: str | None = None

                if inferred_mode == HVACMode.HEAT and rooms_need_heat:
                    mode_to_engage = HVACMode.HEAT
                    consensus_reason = "Trend=HEAT, rooms need heat"
                elif inferred_mode == HVACMode.COOL and rooms_need_cool:
                    mode_to_engage = HVACMode.COOL
                    consensus_reason = "Trend=COOL, rooms need cool"
                elif inferred_mode is None:
                    # Can't infer mode (no sensors/targets) - fall back to what rooms need
                    if rooms_need_heat:
                        mode_to_engage = HVACMode.HEAT
                        consensus_reason = "No trend data, rooms need heat"
                    elif rooms_need_cool:
                        mode_to_engage = HVACMode.COOL
                        consensus_reason = "No trend data, rooms need cool"
                else:
                    # Mismatch: trend doesn't align with what rooms need
                    _LOGGER.debug(
                        "Consensus mismatch: inferred_mode=%s, rooms_need_heat=%s, rooms_need_cool=%s - not turning on",
                        inferred_mode.value if inferred_mode else None,
                        rooms_need_heat,
                        rooms_need_cool,
                    )

                if mode_to_engage:
                    can_on, cycle_reason = self.can_turn_on(now)
                    if can_on:
                        thermostat_state.recommended_action = ThermostatAction.TURN_ON
                        thermostat_state.action_reason = f"{' and '.join(reason_parts)} ({consensus_reason})"
                        # Store the mode to engage so execute_action knows which mode to use
                        thermostat_state.inferred_hvac_mode = mode_to_engage
                    else:
                        thermostat_state.recommended_action = ThermostatAction.WAIT_CYCLE_OFF
                        thermostat_state.action_reason = f"Want to turn on but {cycle_reason}"
                else:
                    # No consensus - don't turn on
                    thermostat_state.recommended_action = ThermostatAction.NONE
                    if inferred_mode == HVACMode.HEAT and rooms_need_cool:
                        thermostat_state.action_reason = (
                            f"Anomaly: house trend is HEAT but rooms need COOL "
                            f"({' and '.join(reason_parts)})"
                        )
                    elif inferred_mode == HVACMode.COOL and rooms_need_heat:
                        thermostat_state.action_reason = (
                            f"Anomaly: house trend is COOL but rooms need HEAT "
                            f"({' and '.join(reason_parts)})"
                        )
                    else:
                        thermostat_state.action_reason = f"No clear mode consensus ({' and '.join(reason_parts)})"
            else:
                thermostat_state.recommended_action = ThermostatAction.NONE
                thermostat_state.action_reason = f"Already on, {' and '.join(reason_parts)}"

        return thermostat_state

    def get_summary(
        self,
        active_areas: list[AreaOccupancyState],
        area_temp_sensors: dict[str, list[str]],
        inactive_areas: list[AreaOccupancyState] | None = None,
        respect_user_off: bool = True,
        eco_mode: bool = False,
    ) -> dict[str, Any]:
        """Get a summary of the current thermostat control state.

        Args:
            active_areas: List of active areas.
            area_temp_sensors: Dict of area_id -> temperature sensor list.
            inactive_areas: List of inactive areas to check for critical temps.
            respect_user_off: Whether to respect user's choice to turn thermostat off.
            eco_mode: Whether eco mode is enabled (only consider active rooms).

        Returns:
            Dict with summary information.
        """
        state = self.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas,
            respect_user_off=respect_user_off,
            eco_mode=eco_mode,
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
            "eco_mode": eco_mode,
            "active_room_count": state.active_room_count,
            "satiated_room_count": state.satiated_room_count,
            "critical_room_count": state.critical_room_count,
            "all_active_rooms_satiated": state.all_active_rooms_satiated,
            "inferred_hvac_mode": state.inferred_hvac_mode.value if state.inferred_hvac_mode else None,
            "rooms_need_heat": state.rooms_need_heat,
            "rooms_need_cool": state.rooms_need_cool,
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

    async def _apply_boost_temperature(
        self,
        target_mode: HVACMode | str,
        thermostat_state: ThermostatState,
    ) -> None:
        """Apply the target temperature (with optional boost offset) to the physical thermostat.

        This ensures the physical thermostat has the correct target temperature set,
        including any away mode adjustments. The optional boost offset raises the heat
        setpoint (or lowers the cool setpoint) to overcome the thermostat's internal
        deadband and ensure it actually calls for heating/cooling.

        Args:
            target_mode: The HVAC mode being set (heat, cool, heat_cool).
            thermostat_state: The current thermostat state with target temperatures.
        """
        # Convert string mode to HVACMode if needed
        if isinstance(target_mode, str):
            try:
                target_mode = HVACMode(target_mode)
            except ValueError:
                _LOGGER.debug("Unknown HVAC mode %s, skipping temperature set", target_mode)
                return

        # Get current target temperatures (these already include away mode adjustments)
        target_temp = thermostat_state.target_temperature
        target_temp_low = thermostat_state.target_temp_low
        target_temp_high = thermostat_state.target_temp_high

        if target_mode == HVACMode.HEAT:
            if target_temp is not None:
                final_temp = target_temp + self._heating_boost_offset
                if self._heating_boost_offset != 0.0:
                    _LOGGER.info(
                        "Setting %s temperature to %.1f (target %.1f + boost %.1f)",
                        self.thermostat_entity_id,
                        final_temp,
                        target_temp,
                        self._heating_boost_offset,
                    )
                else:
                    _LOGGER.info(
                        "Setting %s temperature to %.1f",
                        self.thermostat_entity_id,
                        final_temp,
                    )
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": self.thermostat_entity_id,
                        "temperature": final_temp,
                    },
                    blocking=True,
                )

        elif target_mode == HVACMode.COOL:
            if target_temp is not None:
                final_temp = target_temp - self._cooling_boost_offset
                if self._cooling_boost_offset != 0.0:
                    _LOGGER.info(
                        "Setting %s temperature to %.1f (target %.1f - boost %.1f)",
                        self.thermostat_entity_id,
                        final_temp,
                        target_temp,
                        self._cooling_boost_offset,
                    )
                else:
                    _LOGGER.info(
                        "Setting %s temperature to %.1f",
                        self.thermostat_entity_id,
                        final_temp,
                    )
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": self.thermostat_entity_id,
                        "temperature": final_temp,
                    },
                    blocking=True,
                )

        elif target_mode == HVACMode.HEAT_COOL:
            # For heat_cool mode, set both setpoints
            if target_temp_low is not None or target_temp_high is not None:
                service_data: dict[str, Any] = {"entity_id": self.thermostat_entity_id}

                if target_temp_low is not None:
                    service_data["target_temp_low"] = target_temp_low + self._heating_boost_offset

                if target_temp_high is not None:
                    service_data["target_temp_high"] = target_temp_high - self._cooling_boost_offset

                has_boost = self._heating_boost_offset != 0.0 or self._cooling_boost_offset != 0.0
                if has_boost:
                    _LOGGER.info(
                        "Setting %s temps to low=%.1f, high=%.1f "
                        "(targets: low=%.1f, high=%.1f, boosts: heat=+%.1f, cool=-%.1f)",
                        self.thermostat_entity_id,
                        service_data.get("target_temp_low", 0),
                        service_data.get("target_temp_high", 0),
                        target_temp_low or 0,
                        target_temp_high or 0,
                        self._heating_boost_offset,
                        self._cooling_boost_offset,
                    )
                else:
                    _LOGGER.info(
                        "Setting %s temps to low=%.1f, high=%.1f",
                        self.thermostat_entity_id,
                        service_data.get("target_temp_low", 0),
                        service_data.get("target_temp_high", 0),
                    )
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    service_data,
                    blocking=True,
                )

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
            # Use the inferred HVAC mode from consensus logic
            # This is set during evaluate_thermostat_action based on global trend
            if thermostat_state.inferred_hvac_mode and thermostat_state.inferred_hvac_mode != HVACMode.OFF:
                target_mode = thermostat_state.inferred_hvac_mode
            elif self._previous_hvac_mode and self._previous_hvac_mode != HVACMode.OFF:
                # Fall back to previous mode if no inferred mode
                try:
                    target_mode = HVACMode(self._previous_hvac_mode)
                except ValueError:
                    target_mode = HVACMode.HEAT
            else:
                # Default to heat if nothing else available
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

            # Apply boost offset to temperature setpoint to ensure thermostat calls for heat/cool
            # This overcomes the physical thermostat's internal deadband
            await self._apply_boost_temperature(target_mode, thermostat_state)

            # Set fan to ON when heating/cooling to ensure airflow
            if self.supports_fan_mode():
                fan_on_mode = self._get_best_fan_on_mode()
                if fan_on_mode:
                    current_fan_mode = self.get_fan_mode()
                    # Store previous fan mode if we haven't already
                    if not self._we_changed_fan_mode and current_fan_mode:
                        self._previous_fan_mode = current_fan_mode

                    if current_fan_mode != fan_on_mode:
                        _LOGGER.info(
                            "Setting fan mode to '%s' for active conditioning (was '%s')",
                            fan_on_mode,
                            current_fan_mode,
                        )
                        await self.hass.services.async_call(
                            "climate",
                            "set_fan_mode",
                            {
                                "entity_id": self.thermostat_entity_id,
                                "fan_mode": fan_on_mode,
                            },
                            blocking=True,
                        )
                        self._we_changed_fan_mode = True

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

            # Set fan to AUTO/OFF when not conditioning to save energy
            if self.supports_fan_mode():
                fan_off_mode = self._get_best_fan_off_mode()
                if fan_off_mode:
                    current_fan_mode = self.get_fan_mode()
                    # Store previous fan mode if we haven't already
                    if not self._we_changed_fan_mode and current_fan_mode:
                        self._previous_fan_mode = current_fan_mode

                    if current_fan_mode != fan_off_mode:
                        _LOGGER.info(
                            "Setting fan mode to '%s' when turning off (was '%s')",
                            fan_off_mode,
                            current_fan_mode,
                        )
                        await self.hass.services.async_call(
                            "climate",
                            "set_fan_mode",
                            {
                                "entity_id": self.thermostat_entity_id,
                                "fan_mode": fan_off_mode,
                            },
                            blocking=True,
                        )
                        self._we_changed_fan_mode = True

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
            "previous_hvac_mode": self._previous_hvac_mode,
            "previous_fan_mode": self._previous_fan_mode,
            "we_changed_fan_mode": self._we_changed_fan_mode,
            "stored_target_temp": self._stored_target_temp,
            "stored_target_temp_low": self._stored_target_temp_low,
            "stored_target_temp_high": self._stored_target_temp_high,
            "saved_at": dt_util.utcnow().isoformat(),
        }

        await self._store.async_save(state_data)
        _LOGGER.debug(
            "Saved thermostat controller state: we_turned_off=%s, previous_hvac_mode=%s, "
            "previous_fan_mode=%s, target_temps=(%s, %s, %s)",
            self._we_turned_off,
            self._previous_hvac_mode,
            self._previous_fan_mode,
            self._stored_target_temp,
            self._stored_target_temp_low,
            self._stored_target_temp_high,
        )

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

        if stored_data.get("previous_hvac_mode"):
            self._previous_hvac_mode = stored_data["previous_hvac_mode"]

        # Restore fan mode tracking
        if stored_data.get("previous_fan_mode"):
            self._previous_fan_mode = stored_data["previous_fan_mode"]
        if stored_data.get("we_changed_fan_mode"):
            self._we_changed_fan_mode = True

        # Restore stored target temperatures
        if stored_data.get("stored_target_temp") is not None:
            self._stored_target_temp = stored_data["stored_target_temp"]
        if stored_data.get("stored_target_temp_low") is not None:
            self._stored_target_temp_low = stored_data["stored_target_temp_low"]
        if stored_data.get("stored_target_temp_high") is not None:
            self._stored_target_temp_high = stored_data["stored_target_temp_high"]

        _LOGGER.debug(
            "Restored thermostat controller state: we_turned_off=%s, previous_hvac_mode=%s, "
            "previous_fan_mode=%s, target_temps=(%s, %s, %s) (saved at %s)",
            self._we_turned_off,
            self._previous_hvac_mode,
            self._previous_fan_mode,
            self._stored_target_temp,
            self._stored_target_temp_low,
            self._stored_target_temp_high,
            stored_data.get("saved_at", "unknown"),
        )
