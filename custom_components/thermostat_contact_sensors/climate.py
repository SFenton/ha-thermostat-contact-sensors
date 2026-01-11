"""Climate platform for Thermostat Contact Sensors integration.

This module provides virtual thermostats for each configured area.
These virtual thermostats are always in heat_cool mode, allowing users
to set both heating and cooling target temperatures for each area.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Self

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.util.unit_conversion import TemperatureConverter
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AREA_ENABLED,
    DOMAIN,
)
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)

# Default temperature values in Fahrenheit
DEFAULT_MIN_TEMP = 45.0  # Minimum setpoint temperature (°F)
DEFAULT_MAX_TEMP = 95.0  # Maximum setpoint temperature (°F)
DEFAULT_TARGET_TEMP_LOW = 71.0  # Default heating target (°F)
DEFAULT_TARGET_TEMP_HIGH = 78.0  # Default cooling target (°F)
DEFAULT_TEMP_STEP = 0.5  # Temperature step increment


@dataclass
class VirtualThermostatExtraStoredData(ExtraStoredData):
    """Extra stored data for virtual thermostat."""

    target_temp_low: float
    target_temp_high: float

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {
            "target_temp_low": self.target_temp_low,
            "target_temp_high": self.target_temp_high,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self | None:
        """Initialize extra data from a dict."""
        if data is None:
            return None
        try:
            return cls(
                target_temp_low=float(data["target_temp_low"]),
                target_temp_high=float(data["target_temp_high"]),
            )
        except (KeyError, ValueError, TypeError):
            return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities for each area."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities: list[ClimateEntity] = []

    # Create a virtual thermostat for each enabled area
    for area_id, area_config in coordinator.areas_config.items():
        if area_config.get(CONF_AREA_ENABLED, True):
            entities.append(
                AreaVirtualThermostat(coordinator, entry, area_id)
            )

    # Create the global virtual thermostat
    entities.append(GlobalVirtualThermostat(coordinator, entry))

    async_add_entities(entities)


class AreaVirtualThermostat(CoordinatorEntity, RestoreEntity, ClimateEntity):
    """Virtual thermostat for an area.
    
    This climate entity is always in heat_cool mode, allowing users to set
    both heating and cooling target temperatures. These targets are used
    by the integration to control the physical thermostat based on area
    occupancy and temperature readings.
    """

    _attr_has_entity_name = True
    _attr_hvac_modes = [HVACMode.HEAT_COOL]
    _attr_hvac_mode = HVACMode.HEAT_COOL
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = DEFAULT_TEMP_STEP
    _attr_min_temp = DEFAULT_MIN_TEMP
    _attr_max_temp = DEFAULT_MAX_TEMP

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
        area_id: str,
    ) -> None:
        """Initialize the virtual thermostat."""
        super().__init__(coordinator)
        self._entry = entry
        self._area_id = area_id

        # Get area name from config
        area_config = coordinator.areas_config.get(area_id, {})
        self._area_name = area_config.get("name", area_id.replace("_", " ").title())

        self._attr_unique_id = f"{entry.entry_id}_{area_id}_thermostat"
        self._attr_name = f"{self._area_name} Virtual Thermostat"

        # Initialize target temperatures with defaults
        self._target_temp_low: float = DEFAULT_TARGET_TEMP_LOW
        self._target_temp_high: float = DEFAULT_TARGET_TEMP_HIGH

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        restored = False

        # Try to restore from extra stored data first (more reliable)
        if (extra_data := await self.async_get_last_extra_data()) is not None:
            if (stored := VirtualThermostatExtraStoredData.from_dict(extra_data.as_dict())) is not None:
                self._target_temp_low = stored.target_temp_low
                self._target_temp_high = stored.target_temp_high
                restored = True
                _LOGGER.info(
                    "Restored virtual thermostat %s from extra data: heat=%s, cool=%s",
                    self.entity_id, self._target_temp_low, self._target_temp_high
                )

        # Fall back to restoring from state attributes
        if not restored:
            if (last_state := await self.async_get_last_state()) is not None:
                _LOGGER.debug(
                    "Restoring state for %s: %s", self.entity_id, last_state.state
                )

                # Restore target temperatures from attributes
                if last_state.attributes:
                    if (low := last_state.attributes.get("target_temp_low")) is not None:
                        try:
                            self._target_temp_low = float(low)
                            restored = True
                            _LOGGER.debug(
                                "Restored target_temp_low for %s: %s",
                                self.entity_id, self._target_temp_low
                            )
                        except (ValueError, TypeError):
                            pass

                    if (high := last_state.attributes.get("target_temp_high")) is not None:
                        try:
                            self._target_temp_high = float(high)
                            restored = True
                            _LOGGER.debug(
                                "Restored target_temp_high for %s: %s",
                                self.entity_id, self._target_temp_high
                            )
                        except (ValueError, TypeError):
                            pass

                if restored:
                    _LOGGER.info(
                        "Restored virtual thermostat %s from state: heat=%s, cool=%s",
                        self.entity_id, self._target_temp_low, self._target_temp_high
                    )

        # Register this thermostat with the coordinator
        self._register_with_coordinator()

    @property
    def extra_restore_state_data(self) -> VirtualThermostatExtraStoredData:
        """Return extra state data to be stored for restore on restart."""
        return VirtualThermostatExtraStoredData(
            target_temp_low=self._target_temp_low,
            target_temp_high=self._target_temp_high,
        )

    def _register_with_coordinator(self) -> None:
        """Register this virtual thermostat with the coordinator."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        if not hasattr(coordinator, "area_thermostats"):
            coordinator.area_thermostats = {}
        coordinator.area_thermostats[self._area_id] = self

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode - always heat_cool."""
        return HVACMode.HEAT_COOL

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action based on physical thermostat and room satiation.
        
        Returns heating/cooling if:
        1. The physical thermostat is actively heating/cooling
        2. This room is not satiated (still needs conditioning)
        
        Otherwise returns idle.
        """
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        
        # Get the physical thermostat's hvac_action
        physical_action = coordinator.get_physical_thermostat_hvac_action()
        
        if physical_action in (HVACAction.HEATING, HVACAction.COOLING):
            # Check if this room is satiated
            thermostat_state = coordinator.last_thermostat_state
            if thermostat_state:
                room_state = thermostat_state.room_states.get(self._area_id)
                if room_state and not room_state.is_satiated:
                    # Room needs conditioning and thermostat is active
                    return physical_action
        
        return HVACAction.IDLE

    @property
    def target_temperature_low(self) -> float:
        """Return the low target temperature (heating target).
        
        This returns the base/home value - what the user will feel when home.
        For the actual control target (with away adjustment), use effective_target_temp_low.
        """
        return self._target_temp_low

    @property
    def target_temperature_high(self) -> float:
        """Return the high target temperature (cooling target).
        
        This returns the base/home value - what the user will feel when home.
        For the actual control target (with away adjustment), use effective_target_temp_high.
        """
        return self._target_temp_high

    @property
    def effective_target_temp_low(self) -> float:
        """Return the effective heating target with away adjustment applied."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        if coordinator.is_away and coordinator.away_mode_configured:
            return self._target_temp_low + coordinator.away_heat_temp_diff
        return self._target_temp_low

    @property
    def effective_target_temp_high(self) -> float:
        """Return the effective cooling target with away adjustment applied."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        if coordinator.is_away and coordinator.away_mode_configured:
            return self._target_temp_high + coordinator.away_cool_temp_diff
        return self._target_temp_high

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature from area sensors."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        # Get temperature state for this area from last thermostat evaluation
        thermostat_state = coordinator.last_thermostat_state
        if thermostat_state is None:
            return None

        room_state = thermostat_state.room_states.get(self._area_id)
        if room_state is None:
            return None

        # Return the determining temperature if available
        if room_state.determining_temperature is not None:
            return round(room_state.determining_temperature, 1)

        # If no determining temp, try to get average of all readings
        if room_state.sensor_readings:
            readings = list(room_state.sensor_readings.values())
            return round(sum(readings) / len(readings), 1)

        return None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode - only heat_cool is supported."""
        if hvac_mode != HVACMode.HEAT_COOL:
            _LOGGER.warning(
                "Virtual thermostat %s only supports heat_cool mode, ignoring %s",
                self.entity_id, hvac_mode
            )
            return
        # No action needed, already in heat_cool mode
        self.async_write_ha_state()

    async def async_set_temperature(
        self, _from_global: bool = False, **kwargs: Any
    ) -> None:
        """Set new target temperatures.
        
        The values set here are the base/home temperatures - what the user will
        feel when home. Away mode adjustments are applied internally for control.
        
        Args:
            _from_global: If True, skip notifying global thermostat (to prevent loops)
            **kwargs: Standard Home Assistant climate arguments
        """
        low = kwargs.get("target_temp_low")
        high = kwargs.get("target_temp_high")
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        if low is not None:
            self._target_temp_low = float(low)
            _LOGGER.debug(
                "Set heating target for %s to %s", self._area_id, self._target_temp_low
            )

        if high is not None:
            self._target_temp_high = float(high)
            _LOGGER.debug(
                "Set cooling target for %s to %s", self._area_id, self._target_temp_high
            )

        # Validate that low <= high
        if self._target_temp_low > self._target_temp_high:
            _LOGGER.warning(
                "Heating target (%s) is higher than cooling target (%s) for %s, swapping",
                self._target_temp_low, self._target_temp_high, self._area_id
            )
            self._target_temp_low, self._target_temp_high = (
                self._target_temp_high, self._target_temp_low
            )

        self.async_write_ha_state()

        # Notify global thermostat to recalculate (unless this came from global)
        if not _from_global:
            if hasattr(coordinator, "global_thermostat") and coordinator.global_thermostat:
                coordinator.global_thermostat.async_recalculate_from_areas()

        _LOGGER.info(
            "Virtual thermostat %s targets updated: heat=%s, cool=%s",
            self._area_id, self._target_temp_low, self._target_temp_high
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        # Get area occupancy state
        area_state = coordinator.occupancy_tracker.areas.get(self._area_id)

        attrs = {
            "area_id": self._area_id,
            "area_name": self._area_name,
        }

        if area_state:
            attrs["is_occupied"] = area_state.is_occupied
            attrs["is_active"] = area_state.is_active

        # Get temperature sensors for this area
        area_config = coordinator.areas_config.get(self._area_id, {})
        from .const import CONF_TEMPERATURE_SENSORS
        temp_sensors = area_config.get(CONF_TEMPERATURE_SENSORS, [])
        attrs["temperature_sensors"] = temp_sensors

        # Away mode attributes
        if coordinator.away_mode_configured:
            attrs["away_mode_active"] = coordinator.is_away
            if coordinator.is_away:
                # Show what we're actually targeting right now
                attrs["effective_heat_target"] = self.effective_target_temp_low
                attrs["effective_cool_target"] = self.effective_target_temp_high
                attrs["away_heat_adjustment"] = coordinator.away_heat_temp_diff
                attrs["away_cool_adjustment"] = coordinator.away_cool_temp_diff

        return attrs


@dataclass
class GlobalThermostatExtraStoredData(ExtraStoredData):
    """Extra stored data for global thermostat."""

    target_temp_low: float
    target_temp_high: float
    hvac_mode: str

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {
            "target_temp_low": self.target_temp_low,
            "target_temp_high": self.target_temp_high,
            "hvac_mode": self.hvac_mode,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self | None:
        """Initialize extra data from a dict."""
        if data is None:
            return None
        try:
            return cls(
                target_temp_low=float(data["target_temp_low"]),
                target_temp_high=float(data["target_temp_high"]),
                hvac_mode=str(data.get("hvac_mode", HVACMode.OFF)),
            )
        except (KeyError, ValueError, TypeError):
            return None


class GlobalVirtualThermostat(CoordinatorEntity, RestoreEntity, ClimateEntity):
    """Global virtual thermostat that aggregates all area thermostats.
    
    This climate entity provides a master control over all area thermostats:
    - Heating target = MAX of all area heating targets
    - Cooling target = MIN of all area cooling targets
    
    Supports HEAT, COOL, and OFF modes (no HEAT_COOL).
    
    When the user adjusts this thermostat:
    - Raising heat: All areas with lower heat targets are raised to match
    - Lowering cool: All areas with higher cool targets are lowered to match
    """

    _attr_has_entity_name = True
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
    )
    _attr_temperature_unit = UnitOfTemperature.FAHRENHEIT
    _attr_target_temperature_step = DEFAULT_TEMP_STEP
    _attr_min_temp = DEFAULT_MIN_TEMP
    _attr_max_temp = DEFAULT_MAX_TEMP

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the global virtual thermostat."""
        super().__init__(coordinator)
        self._entry = entry

        self._attr_unique_id = f"{entry.entry_id}_global_thermostat"
        self._attr_name = "Global Virtual Thermostat"

        # Initialize HVAC mode and target temperatures with defaults
        self._hvac_mode: HVACMode = HVACMode.OFF
        self._target_temp_low: float = DEFAULT_TARGET_TEMP_LOW
        self._target_temp_high: float = DEFAULT_TARGET_TEMP_HIGH

    async def async_added_to_hass(self) -> None:
        """Restore state and register with coordinator."""
        await super().async_added_to_hass()

        restored = False

        # Try to restore from extra stored data first
        if (extra_data := await self.async_get_last_extra_data()) is not None:
            if (stored := GlobalThermostatExtraStoredData.from_dict(extra_data.as_dict())) is not None:
                self._target_temp_low = stored.target_temp_low
                self._target_temp_high = stored.target_temp_high
                self._hvac_mode = HVACMode(stored.hvac_mode)
                restored = True
                _LOGGER.info(
                    "Restored global thermostat from extra data: mode=%s, heat=%s, cool=%s",
                    self._hvac_mode, self._target_temp_low, self._target_temp_high
                )

        # Fall back to restoring from state attributes
        if not restored:
            if (last_state := await self.async_get_last_state()) is not None:
                # Restore hvac_mode from state
                if last_state.state in [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]:
                    self._hvac_mode = HVACMode(last_state.state)
                    restored = True
                if last_state.attributes:
                    if (low := last_state.attributes.get("target_temp_low")) is not None:
                        try:
                            self._target_temp_low = float(low)
                            restored = True
                        except (ValueError, TypeError):
                            pass
                    if (high := last_state.attributes.get("target_temp_high")) is not None:
                        try:
                            self._target_temp_high = float(high)
                            restored = True
                        except (ValueError, TypeError):
                            pass
                if restored:
                    _LOGGER.info(
                        "Restored global thermostat from state: mode=%s, heat=%s, cool=%s",
                        self._hvac_mode, self._target_temp_low, self._target_temp_high
                    )

        # Register this thermostat with the coordinator
        self._register_with_coordinator()

        # Recalculate from area thermostats after a brief delay
        # (to ensure all area thermostats are registered first)
        self.hass.async_create_task(self._async_initial_recalculate())

    async def _async_initial_recalculate(self) -> None:
        """Recalculate after initial setup."""
        # Wait for area thermostats to be registered
        await self.hass.async_block_till_done()
        self.async_recalculate_from_areas()

    def _register_with_coordinator(self) -> None:
        """Register this global thermostat with the coordinator."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.global_thermostat = self

    @property
    def extra_restore_state_data(self) -> GlobalThermostatExtraStoredData:
        """Return extra state data to be stored for restore on restart."""
        return GlobalThermostatExtraStoredData(
            target_temp_low=self._target_temp_low,
            target_temp_high=self._target_temp_high,
            hvac_mode=self._hvac_mode,
        )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        return self._hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action based on physical thermostat.
        
        Returns the physical thermostat's hvac_action if any room needs conditioning,
        otherwise returns idle.
        """
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        
        # Get the physical thermostat's hvac_action
        physical_action = coordinator.get_physical_thermostat_hvac_action()
        
        if physical_action in (HVACAction.HEATING, HVACAction.COOLING):
            # Check if any area thermostat is actively conditioning
            if hasattr(coordinator, "area_thermostats"):
                for area_thermostat in coordinator.area_thermostats.values():
                    if area_thermostat.hvac_action == physical_action:
                        return physical_action
        
        return HVACAction.IDLE

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature for HEAT or COOL mode.
        
        This returns the base/home value - what the user will feel when home.
        """
        if self._hvac_mode == HVACMode.HEAT:
            return self.target_temperature_low
        elif self._hvac_mode == HVACMode.COOL:
            return self.target_temperature_high
        return None

    @property
    def target_temperature_low(self) -> float:
        """Return the low target temperature (heating target).
        
        This returns the base/home value - what the user will feel when home.
        For the actual control target (with away adjustment), use effective_target_temp_low.
        """
        return self._target_temp_low

    @property
    def target_temperature_high(self) -> float:
        """Return the high target temperature (cooling target).
        
        This returns the base/home value - what the user will feel when home.
        For the actual control target (with away adjustment), use effective_target_temp_high.
        """
        return self._target_temp_high

    @property
    def effective_target_temp_low(self) -> float:
        """Return the effective heating target with away adjustment applied."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        if coordinator.is_away and coordinator.away_mode_configured:
            return self._target_temp_low + coordinator.away_heat_temp_diff
        return self._target_temp_low

    @property
    def effective_target_temp_high(self) -> float:
        """Return the effective cooling target with away adjustment applied."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        if coordinator.is_away and coordinator.away_mode_configured:
            return self._target_temp_high + coordinator.away_cool_temp_diff
        return self._target_temp_high

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature - average across all areas."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        if not hasattr(coordinator, "area_thermostats"):
            return None

        temps = []
        for area_thermostat in coordinator.area_thermostats.values():
            temp = area_thermostat.current_temperature
            if temp is not None:
                temps.append(temp)

        if temps:
            return round(sum(temps) / len(temps), 1)
        return None

    @callback
    def async_recalculate_from_areas(self) -> None:
        """Recalculate global targets from area thermostats.
        
        Global heating = MAX of all area heating targets
        Global cooling = MIN of all area cooling targets
        """
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        if not hasattr(coordinator, "area_thermostats") or not coordinator.area_thermostats:
            return

        heat_targets = []
        cool_targets = []

        for area_thermostat in coordinator.area_thermostats.values():
            heat_targets.append(area_thermostat.target_temperature_low)
            cool_targets.append(area_thermostat.target_temperature_high)

        if heat_targets:
            new_heat = max(heat_targets)
            if new_heat != self._target_temp_low:
                self._target_temp_low = new_heat
                _LOGGER.debug("Global heat target updated to %s", self._target_temp_low)

        if cool_targets:
            new_cool = min(cool_targets)
            if new_cool != self._target_temp_high:
                self._target_temp_high = new_cool
                _LOGGER.debug("Global cool target updated to %s", self._target_temp_high)

        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode - supports OFF, HEAT, COOL."""
        if hvac_mode not in [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]:
            _LOGGER.warning(
                "Global thermostat only supports OFF, HEAT, COOL modes, ignoring %s",
                hvac_mode
            )
            return
        self._hvac_mode = hvac_mode
        _LOGGER.info("Global thermostat mode set to %s", hvac_mode)
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperatures and propagate to area thermostats.
        
        Propagation logic ensures display consistency:
        - Heat: If new global heat < area's heat, lower that area (ceiling behavior)
        - Cool: If new global cool > area's cool, raise that area (floor behavior)
        
        This ensures when you lower the displayed heat or raise the displayed cool,
        the outlier areas are brought in line and the display reflects your setting.
        """
        new_low = kwargs.get("target_temp_low")
        new_high = kwargs.get("target_temp_high")

        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        if new_low is not None:
            new_low = float(new_low)
            self._target_temp_low = new_low

            # If global heat is lower than an area's heat, lower that area
            # This ensures display consistency (display shows MAX, so lower outliers)
            if hasattr(coordinator, "area_thermostats"):
                for area_id, area_thermostat in coordinator.area_thermostats.items():
                    if area_thermostat.target_temperature_low > new_low:
                        _LOGGER.debug(
                            "Lowering %s heat target from %s to %s",
                            area_id, area_thermostat.target_temperature_low, new_low
                        )
                        # Use _from_global=True to prevent infinite loop
                        await area_thermostat.async_set_temperature(
                            _from_global=True,
                            target_temp_low=new_low,
                            target_temp_high=area_thermostat.target_temperature_high,
                        )

        if new_high is not None:
            new_high = float(new_high)
            self._target_temp_high = new_high

            # If global cool is higher than an area's cool, raise that area
            # This ensures display consistency (display shows MIN, so raise outliers)
            if hasattr(coordinator, "area_thermostats"):
                for area_id, area_thermostat in coordinator.area_thermostats.items():
                    if area_thermostat.target_temperature_high < new_high:
                        _LOGGER.debug(
                            "Raising %s cool target from %s to %s",
                            area_id, area_thermostat.target_temperature_high, new_high
                        )
                        # Use _from_global=True to prevent infinite loop
                        await area_thermostat.async_set_temperature(
                            _from_global=True,
                            target_temp_low=area_thermostat.target_temperature_low,
                            target_temp_high=new_high,
                        )

        # Validate that low <= high
        if self._target_temp_low > self._target_temp_high:
            _LOGGER.warning(
                "Global heating target (%s) is higher than cooling target (%s), swapping",
                self._target_temp_low, self._target_temp_high
            )
            self._target_temp_low, self._target_temp_high = (
                self._target_temp_high, self._target_temp_low
            )

        # Recalculate from areas to ensure display consistency
        # This makes "wrong direction" operations (raise heat, lower cool) into no-ops
        # that visually snap back to the actual MAX(heat)/MIN(cool)
        self.async_recalculate_from_areas()

        _LOGGER.info(
            "Global thermostat targets updated: heat=%s, cool=%s",
            self._target_temp_low, self._target_temp_high
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        attrs = {
            "monitored_areas": [],
            "area_count": 0,
        }

        if hasattr(coordinator, "area_thermostats"):
            attrs["monitored_areas"] = list(coordinator.area_thermostats.keys())
            attrs["area_count"] = len(coordinator.area_thermostats)

            # Show individual area targets (base/home values)
            area_targets = {}
            for area_id, area_thermostat in coordinator.area_thermostats.items():
                area_targets[area_id] = {
                    "heat": area_thermostat.target_temperature_low,
                    "cool": area_thermostat.target_temperature_high,
                }
            attrs["area_targets"] = area_targets

        # Away mode attributes
        if coordinator.away_mode_configured:
            attrs["away_mode_active"] = coordinator.is_away
            if coordinator.is_away:
                # Show what we're actually targeting right now
                attrs["effective_heat_target"] = self.effective_target_temp_low
                attrs["effective_cool_target"] = self.effective_target_temp_high
                attrs["away_heat_adjustment"] = coordinator.away_heat_temp_diff
                attrs["away_cool_adjustment"] = coordinator.away_cool_temp_diff
                attrs["presence_entity"] = coordinator.away_presence_entity

        return attrs
