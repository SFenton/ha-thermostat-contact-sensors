"""The Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CONTACT_SENSORS,
    CONF_THERMOSTAT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)

type ThermostatContactSensorsConfigEntry = ConfigEntry[ThermostatContactSensorsCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermostatContactSensorsConfigEntry
) -> bool:
    """Set up Thermostat Contact Sensors from a config entry."""
    _LOGGER.debug("Setting up Thermostat Contact Sensors: %s", entry.title)

    # Create coordinator
    coordinator = ThermostatContactSensorsCoordinator(
        hass,
        config_entry_id=entry.entry_id,
        contact_sensors=entry.data[CONF_CONTACT_SENSORS],
        thermostat=entry.data[CONF_THERMOSTAT],
        options=dict(entry.options),
    )

    # Store coordinator
    entry.runtime_data = coordinator

    # Set up coordinator
    await coordinator.async_setup()

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _LOGGER.info("Thermostat Contact Sensors setup complete: %s", entry.title)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ThermostatContactSensorsConfigEntry
) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Thermostat Contact Sensors: %s", entry.title)

    # Shut down coordinator
    await entry.runtime_data.async_shutdown()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    return unload_ok


async def async_update_options(
    hass: HomeAssistant, entry: ThermostatContactSensorsConfigEntry
) -> None:
    """Handle options update."""
    _LOGGER.debug("Updating options for: %s", entry.title)
    entry.runtime_data.update_options(dict(entry.options))
