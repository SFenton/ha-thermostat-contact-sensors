"""Binary sensor platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensor entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities = [
        ThermostatPausedBinarySensor(coordinator, entry),
    ]

    async_add_entities(entities)


class ThermostatPausedBinarySensor(CoordinatorEntity, RestoreEntity, BinarySensorEntity):
    """Binary sensor indicating if thermostat is paused."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:thermostat"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_paused"
        self._attr_name = "Thermostat Paused"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug("Restoring state for %s: %s", self.entity_id, last_state.state)

            # Restore coordinator state from entity attributes
            attrs = last_state.attributes
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator

            # Restore paused state
            if last_state.state == "on":
                coordinator.is_paused = True
                coordinator.previous_hvac_mode = attrs.get("previous_mode")
                coordinator.trigger_sensor = None  # Will be re-detected if still open

                _LOGGER.info(
                    "Restored paused state: previous_mode=%s",
                    coordinator.previous_hvac_mode,
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
    def is_on(self) -> bool:
        """Return True if thermostat is paused (not running)."""
        # Note: device_class RUNNING means is_on=True when running
        # We want to show "on" when paused for visibility, so we invert
        return self.coordinator.is_paused

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        attrs = {
            "thermostat": coordinator.thermostat,
            "previous_mode": coordinator.previous_hvac_mode,
            "open_count": coordinator.open_count,
        }

        if coordinator.trigger_sensor:
            state = self.hass.states.get(coordinator.trigger_sensor)
            if state:
                attrs["triggered_by"] = state.attributes.get(
                    "friendly_name", coordinator.trigger_sensor
                )
            else:
                attrs["triggered_by"] = coordinator.trigger_sensor

        return attrs
