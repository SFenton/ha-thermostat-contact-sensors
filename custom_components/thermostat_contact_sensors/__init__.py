"""The Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.exceptions import ServiceValidationError

from .const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CONTACT_SENSORS,
    CONF_SENSORS,
    CONF_TEMPERATURE_SENSORS,
    CONF_THERMOSTAT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)

# Service constants
SERVICE_PAUSE = "pause"
SERVICE_RESUME = "resume"
SERVICE_RECALCULATE = "recalculate"
ATTR_ENTRY_ID = "entry_id"

# Service schema
SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): str,
    }
)

# Type alias for ConfigEntry with our coordinator (Python 3.9+ compatible)
ThermostatContactSensorsConfigEntry = ConfigEntry


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        # Version 1 -> 2: Add areas configuration
        new_data = {**config_entry.data}

        # Build areas config from legacy contact sensors
        legacy_sensors = new_data.get(CONF_CONTACT_SENSORS, [])

        # Create a simple area config with all legacy sensors in an "uncategorized" area
        # In practice, users should reconfigure after upgrade
        new_data[CONF_AREAS] = {}

        # Try to assign sensors to their actual areas
        entity_reg = er.async_get(hass)
        area_reg = ar.async_get(hass)

        # Group sensors by area
        sensors_by_area: dict[str, list[str]] = {}
        for sensor_id in legacy_sensors:
            entity = entity_reg.async_get(sensor_id)
            if entity and entity.area_id:
                if entity.area_id not in sensors_by_area:
                    sensors_by_area[entity.area_id] = []
                sensors_by_area[entity.area_id].append(sensor_id)

        # Create area configs
        for area_id, sensors in sensors_by_area.items():
            new_data[CONF_AREAS][area_id] = {
                CONF_AREA_ID: area_id,
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: sensors,
                CONF_TEMPERATURE_SENSORS: [],
                CONF_SENSORS: [],
            }

        # Update entry data and version
        hass.config_entries.async_update_entry(config_entry, data=new_data)
        config_entry.version = 2

        _LOGGER.info("Migration to version 2 successful")

    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermostatContactSensorsConfigEntry
) -> bool:
    """Set up Thermostat Contact Sensors from a config entry."""
    _LOGGER.debug("Setting up Thermostat Contact Sensors: %s", entry.title)

    # Get contact sensors from legacy config or from areas config
    contact_sensors = entry.data.get(CONF_CONTACT_SENSORS, [])

    # Get areas config
    areas_config = entry.data.get(CONF_AREAS, {})

    # If using new areas config, gather all binary sensors from enabled areas
    if areas_config:
        contact_sensors = []
        for area_id, area_config in areas_config.items():
            if area_config.get(CONF_AREA_ENABLED, True):
                contact_sensors.extend(area_config.get(CONF_BINARY_SENSORS, []))

    # Create coordinator
    coordinator = ThermostatContactSensorsCoordinator(
        hass,
        config_entry_id=entry.entry_id,
        contact_sensors=contact_sensors,
        thermostat=entry.data[CONF_THERMOSTAT],
        options=dict(entry.options),
        areas_config=areas_config,
    )

    # Store coordinator
    entry.runtime_data = coordinator

    # Set up coordinator
    await coordinator.async_setup()

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once for the domain)
    await _async_setup_services(hass)

    # Register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _LOGGER.info("Thermostat Contact Sensors setup complete: %s", entry.title)
    return True


def _get_coordinator_by_entry_id(
    hass: HomeAssistant, entry_id: str
) -> ThermostatContactSensorsCoordinator:
    """Get coordinator by entry ID."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id:
            return entry.runtime_data
    raise ServiceValidationError(
        f"Config entry {entry_id} not found",
        translation_domain=DOMAIN,
        translation_key="entry_not_found",
    )


async def _async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for the integration."""
    # Only register services once
    if hass.services.has_service(DOMAIN, SERVICE_PAUSE):
        return

    async def async_handle_pause(call: ServiceCall) -> None:
        """Handle the pause service call."""
        entry_id = call.data[ATTR_ENTRY_ID]
        coordinator = _get_coordinator_by_entry_id(hass, entry_id)

        if coordinator.is_paused:
            _LOGGER.info("Thermostat already paused")
            return

        # Manually trigger pause
        await coordinator._async_open_timeout_expired()
        _LOGGER.info("Thermostat paused via service call")

    async def async_handle_resume(call: ServiceCall) -> None:
        """Handle the resume service call."""
        entry_id = call.data[ATTR_ENTRY_ID]
        coordinator = _get_coordinator_by_entry_id(hass, entry_id)

        if not coordinator.is_paused:
            _LOGGER.info("Thermostat not paused")
            return

        # Manually trigger resume
        await coordinator._async_close_timeout_expired()
        _LOGGER.info("Thermostat resumed via service call")

    async def async_handle_recalculate(call: ServiceCall) -> None:
        """Handle the recalculate service call."""
        entry_id = call.data[ATTR_ENTRY_ID]
        coordinator = _get_coordinator_by_entry_id(hass, entry_id)

        # Force recalculation and execute any recommended actions
        await coordinator.async_update_thermostat_state()
        await coordinator.async_update_vents()
        coordinator.async_set_updated_data(None)
        _LOGGER.info("Thermostat state recalculated via service call")

    hass.services.async_register(
        DOMAIN, SERVICE_PAUSE, async_handle_pause, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESUME, async_handle_resume, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALCULATE, async_handle_recalculate, schema=SERVICE_SCHEMA
    )


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

    # When areas or thermostat change, we need to reload the integration
    # to rebuild the coordinator with new sensors
    await hass.config_entries.async_reload(entry.entry_id)
