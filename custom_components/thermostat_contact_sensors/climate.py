"""Climate platform for Thermostat Contact Sensors integration.

This module provides virtual thermostats for each configured area.
These virtual thermostats are always in heat_cool mode, allowing users
to set both heating and cooling target temperatures for each area.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AREA_ENABLED,
    DOMAIN,
)
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)

# Default temperature values
DEFAULT_MIN_TEMP = 7.0  # Minimum setpoint temperature (°C)
DEFAULT_MAX_TEMP = 35.0  # Maximum setpoint temperature (°C)
DEFAULT_TARGET_TEMP_LOW = 18.0  # Default heating target (°C)
DEFAULT_TARGET_TEMP_HIGH = 24.0  # Default cooling target (°C)
DEFAULT_TEMP_STEP = 0.5  # Temperature step increment


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
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
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

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )

            # Restore target temperatures from attributes
            if last_state.attributes:
                if (low := last_state.attributes.get("target_temp_low")) is not None:
                    try:
                        self._target_temp_low = float(low)
                        _LOGGER.debug(
                            "Restored target_temp_low for %s: %s",
                            self.entity_id, self._target_temp_low
                        )
                    except (ValueError, TypeError):
                        pass

                if (high := last_state.attributes.get("target_temp_high")) is not None:
                    try:
                        self._target_temp_high = float(high)
                        _LOGGER.debug(
                            "Restored target_temp_high for %s: %s",
                            self.entity_id, self._target_temp_high
                        )
                    except (ValueError, TypeError):
                        pass

            _LOGGER.info(
                "Restored virtual thermostat %s: heat=%s, cool=%s",
                self.entity_id, self._target_temp_low, self._target_temp_high
            )

        # Register this thermostat with the coordinator
        self._register_with_coordinator()

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
    def target_temperature_low(self) -> float:
        """Return the low target temperature (heating target)."""
        return self._target_temp_low

    @property
    def target_temperature_high(self) -> float:
        """Return the high target temperature (cooling target)."""
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
            return room_state.determining_temperature

        # If no determining temp, try to get average of all readings
        if room_state.sensor_readings:
            readings = list(room_state.sensor_readings.values())
            return sum(readings) / len(readings)

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

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperatures."""
        low = kwargs.get("target_temp_low")
        high = kwargs.get("target_temp_high")

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

        return attrs
