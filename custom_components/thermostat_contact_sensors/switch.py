"""Switch platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities = [
        RespectUserOffSwitch(coordinator, entry),
        EcoModeSwitch(coordinator, entry),
    ]

    async_add_entities(entities)


class RespectUserOffSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control whether to respect user's manual thermostat off state.
    
    When OFF (default): Integration will always turn thermostat back on when
    windows close, even if user had manually turned it off.
    
    When ON: Integration will respect the user's choice. If the thermostat
    was off before the pause, it will stay off after windows close.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-cog"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_respect_user_off"
        self._attr_name = "Respect User Off"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            coordinator.respect_user_off = last_state.state == "on"
            _LOGGER.info(
                "Restored respect_user_off state: %s", coordinator.respect_user_off
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
        """Return True if respecting user's off state."""
        return self.coordinator.respect_user_off

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on - respect user's manual off choice."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.respect_user_off = True
        _LOGGER.info("Respect user off enabled - will not override manual off state")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off - always resume thermostat when windows close."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.respect_user_off = False
        _LOGGER.info("Respect user off disabled - will always resume thermostat")
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "description": (
                "When ON: Respects user's choice to keep thermostat off. "
                "When OFF: Always resumes thermostat when windows close."
            ),
        }


class EcoModeSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control eco mode for thermostat control.
    
    When OFF (default): Thermostat activates based on all rooms including
    unoccupied rooms with critical temperatures.
    
    When ON: Thermostat only activates based on active (occupied) rooms.
    Unoccupied rooms will not trigger thermostat activation, even if they
    reach critical temperatures. The existing anomaly detection still applies -
    if an active room needs cooling but the house trends towards needing heat,
    the thermostat will not activate.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:leaf"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_eco_mode"
        self._attr_name = "Eco Mode"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            coordinator.eco_mode = last_state.state == "on"
            _LOGGER.info(
                "Restored eco_mode state: %s", coordinator.eco_mode
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
        """Return True if eco mode is enabled."""
        return self.coordinator.eco_mode

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on eco mode - only consider active rooms."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.eco_mode = True
        _LOGGER.info("Eco mode enabled - thermostat will only respond to active (occupied) rooms")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off eco mode - consider all rooms including critical unoccupied ones."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.eco_mode = False
        _LOGGER.info("Eco mode disabled - thermostat will respond to all rooms including unoccupied critical rooms")
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "description": (
                "When ON: Thermostat only activates for active (occupied) rooms. "
                "When OFF: Thermostat also activates for unoccupied rooms with critical temperatures."
            ),
        }
