"""Config flow for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREA_MIN_VENTS_OPEN,
    CONF_AREA_VENT_OPEN_DELAY_SECONDS,
    CONF_AREAS,
    CONF_AWAY_COOL_TEMP_DIFF,
    CONF_AWAY_HEAT_TEMP_DIFF,
    CONF_AWAY_PRESENCE_ENTITY,
    CONF_BINARY_SENSORS,
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_GRACE_PERIOD_MINUTES,
    CONF_MIN_CYCLE_OFF_MINUTES,
    CONF_MIN_CYCLE_ON_MINUTES,
    CONF_MIN_OCCUPANCY_MINUTES,
    CONF_MIN_VENTS_OPEN,
    CONF_NOTIFICATION_TAG,
    CONF_NOTIFY_MESSAGE_PAUSED,
    CONF_NOTIFY_MESSAGE_RESUMED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE_PAUSED,
    CONF_NOTIFY_TITLE_RESUMED,
    CONF_OPEN_TIMEOUT,
    CONF_SENSORS,
    CONF_TEMPERATURE_DEADBAND,
    CONF_TEMPERATURE_SENSORS,
    CONF_THERMOSTAT,
    CONF_UNOCCUPIED_COOLING_THRESHOLD,
    CONF_UNOCCUPIED_HEATING_THRESHOLD,
    CONF_VENT_DEBOUNCE_SECONDS,
    CONF_VENT_OPEN_DELAY_SECONDS,
    CONF_VENTS,
    DEFAULT_AWAY_COOL_TEMP_DIFF,
    DEFAULT_AWAY_HEAT_TEMP_DIFF,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_MIN_CYCLE_OFF_MINUTES,
    DEFAULT_MIN_CYCLE_ON_MINUTES,
    DEFAULT_MIN_OCCUPANCY_MINUTES,
    DEFAULT_MIN_VENTS_OPEN,
    DEFAULT_NOTIFICATION_TAG,
    DEFAULT_NOTIFY_MESSAGE_PAUSED,
    DEFAULT_NOTIFY_MESSAGE_RESUMED,
    DEFAULT_NOTIFY_TITLE_PAUSED,
    DEFAULT_NOTIFY_TITLE_RESUMED,
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_TEMPERATURE_DEADBAND,
    DEFAULT_UNOCCUPIED_COOLING_THRESHOLD,
    DEFAULT_UNOCCUPIED_HEATING_THRESHOLD,
    DEFAULT_VENT_DEBOUNCE_SECONDS,
    DEFAULT_VENT_OPEN_DELAY_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


# Device classes for contact sensors (door/window sensors that trigger pause)
CONTACT_SENSOR_DEVICE_CLASSES = {"door", "window", "garage_door", "opening"}


def get_areas_with_sensors(hass: HomeAssistant) -> dict[str, dict]:
    """Get all areas and their associated sensors.

    Returns a dict of area_id -> {
        "name": str,
        "binary_sensors": list of entity_ids (for occupancy - motion, presence, etc.),
        "contact_sensors": list of entity_ids (door/window sensors for pause),
        "temperature_sensors": list of entity_ids,
        "sensors": list of entity_ids (non-temperature),
        "covers": list of entity_ids (cover domain entities),
    }
    """
    area_reg = ar.async_get(hass)
    entity_reg = er.async_get(hass)

    areas_data = {}

    for area in area_reg.async_list_areas():
        areas_data[area.id] = {
            "name": area.name,
            "binary_sensors": [],
            "contact_sensors": [],
            "temperature_sensors": [],
            "sensors": [],
            "covers": [],
        }

    # Go through all entities and categorize them by area
    for entity in entity_reg.entities.values():
        if entity.area_id is None:
            continue

        if entity.area_id not in areas_data:
            continue

        # Skip disabled entities
        if entity.disabled:
            continue

        entity_id = entity.entity_id

        if entity.domain == BINARY_SENSOR_DOMAIN:
            # Check if it's a contact sensor (door/window) by device_class
            device_class = entity.device_class or entity.original_device_class
            if device_class in CONTACT_SENSOR_DEVICE_CLASSES:
                areas_data[entity.area_id]["contact_sensors"].append(entity_id)
            else:
                # Other binary sensors (motion, presence, etc.) for occupancy
                areas_data[entity.area_id]["binary_sensors"].append(entity_id)
        elif entity.domain == SENSOR_DOMAIN:
            # Check if it's a temperature sensor by device_class
            if entity.original_device_class == "temperature" or (
                entity.device_class == "temperature"
            ):
                areas_data[entity.area_id]["temperature_sensors"].append(entity_id)
            else:
                areas_data[entity.area_id]["sensors"].append(entity_id)
        elif entity.domain == COVER_DOMAIN:
            areas_data[entity.area_id]["covers"].append(entity_id)

    return areas_data


def build_default_areas_config(hass: HomeAssistant) -> dict[str, dict]:
    """Build default area configuration with all areas and sensors enabled."""
    areas_data = get_areas_with_sensors(hass)
    areas_config = {}

    for area_id, area_info in areas_data.items():
        areas_config[area_id] = {
            CONF_AREA_ID: area_id,
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: area_info["binary_sensors"],
            CONF_CONTACT_SENSORS: area_info["contact_sensors"],
            CONF_TEMPERATURE_SENSORS: area_info["temperature_sensors"],
            CONF_SENSORS: area_info["sensors"],
            CONF_VENTS: [],  # Vents are not auto-assigned
        }

    return areas_config


class ThermostatContactSensorsConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Thermostat Contact Sensors."""

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate inputs
            if not user_input.get(CONF_THERMOSTAT):
                errors[CONF_THERMOSTAT] = "no_thermostat_selected"
            else:
                # Create a unique ID based on the thermostat
                await self.async_set_unique_id(user_input[CONF_THERMOSTAT])
                self._abort_if_unique_id_configured()

                name = user_input.get(CONF_NAME, "Thermostat Contact Sensors")

                # Build default areas configuration
                areas_config = build_default_areas_config(self.hass)

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_THERMOSTAT: user_input[CONF_THERMOSTAT],
                        CONF_AREAS: areas_config,
                        # Keep legacy field for backwards compatibility
                        CONF_CONTACT_SENSORS: user_input.get(CONF_CONTACT_SENSORS, []),
                    },
                    options={
                        CONF_OPEN_TIMEOUT: user_input.get(
                            CONF_OPEN_TIMEOUT, DEFAULT_OPEN_TIMEOUT
                        ),
                        CONF_CLOSE_TIMEOUT: user_input.get(
                            CONF_CLOSE_TIMEOUT, DEFAULT_CLOSE_TIMEOUT
                        ),
                        CONF_NOTIFY_SERVICE: user_input.get(CONF_NOTIFY_SERVICE, ""),
                        CONF_NOTIFY_TITLE_PAUSED: user_input.get(
                            CONF_NOTIFY_TITLE_PAUSED, DEFAULT_NOTIFY_TITLE_PAUSED
                        ),
                        CONF_NOTIFY_MESSAGE_PAUSED: user_input.get(
                            CONF_NOTIFY_MESSAGE_PAUSED, DEFAULT_NOTIFY_MESSAGE_PAUSED
                        ),
                        CONF_NOTIFY_TITLE_RESUMED: user_input.get(
                            CONF_NOTIFY_TITLE_RESUMED, DEFAULT_NOTIFY_TITLE_RESUMED
                        ),
                        CONF_NOTIFY_MESSAGE_RESUMED: user_input.get(
                            CONF_NOTIFY_MESSAGE_RESUMED, DEFAULT_NOTIFY_MESSAGE_RESUMED
                        ),
                        CONF_NOTIFICATION_TAG: user_input.get(
                            CONF_NOTIFICATION_TAG, DEFAULT_NOTIFICATION_TAG
                        ),
                    },
                )

        # Build schema for config flow
        data_schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Thermostat Contact Sensors"): str,
                vol.Required(CONF_THERMOSTAT): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=CLIMATE_DOMAIN,
                        multiple=False,
                    )
                ),
                vol.Optional(
                    CONF_OPEN_TIMEOUT, default=DEFAULT_OPEN_TIMEOUT
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_CLOSE_TIMEOUT, default=DEFAULT_CLOSE_TIMEOUT
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_NOTIFY_SERVICE, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "notify_hint": "Enter a notify service like 'notify.mobile_app_phone' or leave empty to disable notifications"
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return ThermostatContactSensorsOptionsFlow(config_entry)


class ThermostatContactSensorsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Thermostat Contact Sensors."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        self._selected_area_id: str | None = None

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        """Return the config entry."""
        return self._config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show the main menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["manage_areas", "configure_area_sensors", "global_settings", "thermostat"],
        )

    async def async_step_thermostat(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle thermostat selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_THERMOSTAT):
                errors[CONF_THERMOSTAT] = "no_thermostat_selected"
            else:
                # Update thermostat in config entry data
                new_data = {
                    **self.config_entry.data,
                    CONF_THERMOSTAT: user_input[CONF_THERMOSTAT],
                }
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=new_data,
                )
                return self.async_create_entry(
                    title="", data=self.config_entry.options
                )

        data = self.config_entry.data

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_THERMOSTAT,
                    default=data.get(CONF_THERMOSTAT, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=CLIMATE_DOMAIN,
                        multiple=False,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="thermostat",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle global settings (timeouts, notifications)."""
        if user_input is not None:
            # Remove empty entity values that entity selectors can't handle
            if CONF_AWAY_PRESENCE_ENTITY in user_input and not user_input[CONF_AWAY_PRESENCE_ENTITY]:
                del user_input[CONF_AWAY_PRESENCE_ENTITY]
            # Merge with existing options
            new_options = {**self.config_entry.options, **user_input}
            # If away presence entity was cleared, make sure to remove it from options
            if CONF_AWAY_PRESENCE_ENTITY not in user_input and CONF_AWAY_PRESENCE_ENTITY in new_options:
                # User didn't provide a value - keep the existing one if any
                pass
            return self.async_create_entry(title="", data=new_options)

        options = self.config_entry.options

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MIN_OCCUPANCY_MINUTES,
                    default=options.get(
                        CONF_MIN_OCCUPANCY_MINUTES, DEFAULT_MIN_OCCUPANCY_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_GRACE_PERIOD_MINUTES,
                    default=options.get(
                        CONF_GRACE_PERIOD_MINUTES, DEFAULT_GRACE_PERIOD_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=2,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_TEMPERATURE_DEADBAND,
                    default=options.get(
                        CONF_TEMPERATURE_DEADBAND, DEFAULT_TEMPERATURE_DEADBAND
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.1,
                        max=5.0,
                        step=0.1,
                        unit_of_measurement="°",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_MIN_CYCLE_ON_MINUTES,
                    default=options.get(
                        CONF_MIN_CYCLE_ON_MINUTES, DEFAULT_MIN_CYCLE_ON_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=30,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_MIN_CYCLE_OFF_MINUTES,
                    default=options.get(
                        CONF_MIN_CYCLE_OFF_MINUTES, DEFAULT_MIN_CYCLE_OFF_MINUTES
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=30,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_UNOCCUPIED_HEATING_THRESHOLD,
                    default=options.get(
                        CONF_UNOCCUPIED_HEATING_THRESHOLD,
                        DEFAULT_UNOCCUPIED_HEATING_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=10.0,
                        step=0.1,
                        unit_of_measurement="°",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_UNOCCUPIED_COOLING_THRESHOLD,
                    default=options.get(
                        CONF_UNOCCUPIED_COOLING_THRESHOLD,
                        DEFAULT_UNOCCUPIED_COOLING_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.5,
                        max=10.0,
                        step=0.1,
                        unit_of_measurement="°",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_OPEN_TIMEOUT,
                    default=options.get(CONF_OPEN_TIMEOUT, DEFAULT_OPEN_TIMEOUT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_CLOSE_TIMEOUT,
                    default=options.get(CONF_CLOSE_TIMEOUT, DEFAULT_CLOSE_TIMEOUT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=60,
                        step=1,
                        unit_of_measurement="minutes",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_SERVICE,
                    default=options.get(CONF_NOTIFY_SERVICE, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_TITLE_PAUSED,
                    default=options.get(
                        CONF_NOTIFY_TITLE_PAUSED, DEFAULT_NOTIFY_TITLE_PAUSED
                    ),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_MESSAGE_PAUSED,
                    default=options.get(
                        CONF_NOTIFY_MESSAGE_PAUSED, DEFAULT_NOTIFY_MESSAGE_PAUSED
                    ),
                ): selector.TemplateSelector(),
                vol.Optional(
                    CONF_NOTIFY_TITLE_RESUMED,
                    default=options.get(
                        CONF_NOTIFY_TITLE_RESUMED, DEFAULT_NOTIFY_TITLE_RESUMED
                    ),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_MESSAGE_RESUMED,
                    default=options.get(
                        CONF_NOTIFY_MESSAGE_RESUMED, DEFAULT_NOTIFY_MESSAGE_RESUMED
                    ),
                ): selector.TemplateSelector(),
                vol.Optional(
                    CONF_NOTIFICATION_TAG,
                    default=options.get(
                        CONF_NOTIFICATION_TAG, DEFAULT_NOTIFICATION_TAG
                    ),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                ),
                # Vent control settings
                vol.Optional(
                    CONF_MIN_VENTS_OPEN,
                    default=options.get(CONF_MIN_VENTS_OPEN, DEFAULT_MIN_VENTS_OPEN),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=20,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_VENT_OPEN_DELAY_SECONDS,
                    default=options.get(
                        CONF_VENT_OPEN_DELAY_SECONDS, DEFAULT_VENT_OPEN_DELAY_SECONDS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=300,
                        step=5,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_VENT_DEBOUNCE_SECONDS,
                    default=options.get(
                        CONF_VENT_DEBOUNCE_SECONDS, DEFAULT_VENT_DEBOUNCE_SECONDS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=300,
                        step=5,
                        unit_of_measurement="seconds",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                # Away mode settings
                vol.Optional(
                    CONF_AWAY_PRESENCE_ENTITY,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["person", "group", "binary_sensor", "input_boolean"],
                        multiple=False,
                    )
                ),
                vol.Optional(
                    CONF_AWAY_HEAT_TEMP_DIFF,
                    default=options.get(
                        CONF_AWAY_HEAT_TEMP_DIFF, DEFAULT_AWAY_HEAT_TEMP_DIFF
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-10.0,
                        max=0.0,
                        step=0.1,
                        unit_of_measurement="°",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Optional(
                    CONF_AWAY_COOL_TEMP_DIFF,
                    default=options.get(
                        CONF_AWAY_COOL_TEMP_DIFF, DEFAULT_AWAY_COOL_TEMP_DIFF
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0,
                        max=10.0,
                        step=0.1,
                        unit_of_measurement="°",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        # Add suggested values for the entity selector (can't use default for optional entity selectors)
        suggested_values = {}
        if options.get(CONF_AWAY_PRESENCE_ENTITY):
            suggested_values[CONF_AWAY_PRESENCE_ENTITY] = options[CONF_AWAY_PRESENCE_ENTITY]
        
        if suggested_values:
            data_schema = self.add_suggested_values_to_schema(data_schema, suggested_values)

        return self.async_show_form(
            step_id="global_settings",
            data_schema=data_schema,
        )

    async def async_step_manage_areas(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show multi-select for enabling/disabling areas."""
        # Get current areas config
        areas_config = dict(self.config_entry.data.get(CONF_AREAS, {}))

        # Get fresh area data from Home Assistant
        areas_data = get_areas_with_sensors(self.hass)

        if not areas_data:
            return self.async_abort(reason="no_areas_found")

        if user_input is not None:
            # Get the list of enabled areas from the multi-select
            enabled_area_ids = user_input.get("enabled_areas", [])

            # Update enabled state for each area
            for area_id, area_info in areas_data.items():
                is_enabled = area_id in enabled_area_ids

                if area_id not in areas_config:
                    # Create new area config
                    areas_config[area_id] = {
                        CONF_AREA_ID: area_id,
                        CONF_AREA_ENABLED: is_enabled,
                        CONF_BINARY_SENSORS: area_info["binary_sensors"],
                        CONF_TEMPERATURE_SENSORS: area_info["temperature_sensors"],
                        CONF_SENSORS: area_info["sensors"],
                    }
                else:
                    # Update existing
                    areas_config[area_id][CONF_AREA_ENABLED] = is_enabled

            # Save the updated config
            new_data = {
                **self.config_entry.data,
                CONF_AREAS: areas_config,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=new_data,
            )

            return self.async_create_entry(title="", data=self.config_entry.options)

        # Build the list of options with area names and sensor counts
        area_options = []
        currently_enabled = []

        for area_id, area_info in areas_data.items():
            # Get sensor count from saved config if available, otherwise from entity registry
            if area_id in areas_config:
                saved_config = areas_config[area_id]
                sensor_count = (
                    len(saved_config.get(CONF_BINARY_SENSORS, []))
                    + len(saved_config.get(CONF_TEMPERATURE_SENSORS, []))
                    + len(saved_config.get(CONF_SENSORS, []))
                )
            else:
                sensor_count = (
                    len(area_info["binary_sensors"])
                    + len(area_info["temperature_sensors"])
                    + len(area_info["sensors"])
                )
            area_options.append(
                selector.SelectOptionDict(
                    value=area_id,
                    label=f"{area_info['name']} ({sensor_count} sensors)",
                )
            )

            # Check if currently enabled
            is_enabled = areas_config.get(area_id, {}).get(CONF_AREA_ENABLED, True)
            if is_enabled:
                currently_enabled.append(area_id)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    "enabled_areas",
                    default=currently_enabled,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=area_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="manage_areas",
            data_schema=data_schema,
        )

    async def async_step_configure_area_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show menu to select an area to configure its sensors."""
        # Get current areas config
        areas_config = self.config_entry.data.get(CONF_AREAS, {})

        # Get fresh area data from Home Assistant
        areas_data = get_areas_with_sensors(self.hass)

        # Build menu options dynamically based on areas
        # Use a dict to map step IDs to display labels
        menu_options = {}
        for area_id, area_info in areas_data.items():
            # Check if area is enabled in config
            is_enabled = areas_config.get(area_id, {}).get(CONF_AREA_ENABLED, True)
            status = "✓" if is_enabled else "○"

            # Get sensor count from saved config if available, otherwise from entity registry
            if area_id in areas_config:
                saved_config = areas_config[area_id]
                sensor_count = (
                    len(saved_config.get(CONF_BINARY_SENSORS, []))
                    + len(saved_config.get(CONF_CONTACT_SENSORS, []))
                    + len(saved_config.get(CONF_TEMPERATURE_SENSORS, []))
                    + len(saved_config.get(CONF_SENSORS, []))
                )
            else:
                sensor_count = (
                    len(area_info["binary_sensors"])
                    + len(area_info["contact_sensors"])
                    + len(area_info["temperature_sensors"])
                    + len(area_info["sensors"])
                )

            step_id = f"area_{area_id}"
            menu_options[step_id] = f"{status} {area_info['name']} ({sensor_count} sensors)"

        if not menu_options:
            # No areas found, show a message
            return self.async_abort(reason="no_areas_found")

        return self.async_show_menu(
            step_id="configure_area_sensors",
            menu_options=menu_options,
        )

    async def async_step_area_config(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Configure a specific area's sensors."""
        area_id = self._selected_area_id

        if area_id is None:
            return self.async_abort(reason="area_not_found")

        # Get area info
        areas_data = get_areas_with_sensors(self.hass)
        if area_id not in areas_data:
            return self.async_abort(reason="area_not_found")

        area_info = areas_data[area_id]

        # Get current config for this area
        areas_config = dict(self.config_entry.data.get(CONF_AREAS, {}))
        current_area_config = areas_config.get(area_id, {})

        if user_input is not None:
            # Save the area configuration
            areas_config[area_id] = {
                CONF_AREA_ID: area_id,
                CONF_AREA_ENABLED: user_input.get(CONF_AREA_ENABLED, True),
                CONF_BINARY_SENSORS: user_input.get(CONF_BINARY_SENSORS, []),
                CONF_CONTACT_SENSORS: user_input.get(CONF_CONTACT_SENSORS, []),
                CONF_TEMPERATURE_SENSORS: user_input.get(CONF_TEMPERATURE_SENSORS, []),
                CONF_SENSORS: user_input.get(CONF_SENSORS, []),
                CONF_VENTS: user_input.get(CONF_VENTS, []),
            }

            # Add per-area vent overrides if specified
            if user_input.get(CONF_AREA_VENT_OPEN_DELAY_SECONDS) is not None:
                areas_config[area_id][CONF_AREA_VENT_OPEN_DELAY_SECONDS] = user_input[
                    CONF_AREA_VENT_OPEN_DELAY_SECONDS
                ]
            if user_input.get(CONF_AREA_MIN_VENTS_OPEN) is not None:
                areas_config[area_id][CONF_AREA_MIN_VENTS_OPEN] = user_input[
                    CONF_AREA_MIN_VENTS_OPEN
                ]

            # Update config entry
            new_data = {
                **self.config_entry.data,
                CONF_AREAS: areas_config,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data=new_data,
            )

            # Go back to configure area sensors menu
            return await self.async_step_configure_area_sensors()

        # Build the form schema
        schema_dict = {
            vol.Optional(
                CONF_AREA_ENABLED,
                default=current_area_config.get(CONF_AREA_ENABLED, True),
            ): selector.BooleanSelector(),
        }

        # Always show contact sensors field (door/window sensors that trigger thermostat pause)
        schema_dict[vol.Optional(
            CONF_CONTACT_SENSORS,
            default=current_area_config.get(
                CONF_CONTACT_SENSORS, area_info.get("contact_sensors", [])
            ),
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=BINARY_SENSOR_DOMAIN,
                multiple=True,
            )
        )

        # Always show binary sensors field so users can add sensors
        # even if none were auto-detected in this area (for occupancy detection)
        schema_dict[vol.Optional(
            CONF_BINARY_SENSORS,
            default=current_area_config.get(
                CONF_BINARY_SENSORS, area_info.get("binary_sensors", [])
            ),
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=BINARY_SENSOR_DOMAIN,
                multiple=True,
            )
        )

        # Always show temperature sensors field so users can add sensors
        # even if none were auto-detected with device_class=temperature
        schema_dict[vol.Optional(
            CONF_TEMPERATURE_SENSORS,
            default=current_area_config.get(
                CONF_TEMPERATURE_SENSORS, area_info.get("temperature_sensors", [])
            ),
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=SENSOR_DOMAIN,
                multiple=True,
            )
        )

        # Always show other sensors field so users can add sensors
        schema_dict[vol.Optional(
            CONF_SENSORS,
            default=current_area_config.get(CONF_SENSORS, area_info.get("sensors", [])),
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=SENSOR_DOMAIN,
                multiple=True,
            )
        )

        # Always show vents field so users can add vents
        schema_dict[vol.Optional(
            CONF_VENTS,
            default=current_area_config.get(CONF_VENTS, []),
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=COVER_DOMAIN,
                multiple=True,
            )
        )

        # Per-area vent override options (optional - leave blank to use global)
        # Use a special "not set" indicator since None can't be easily represented
        current_delay = current_area_config.get(CONF_AREA_VENT_OPEN_DELAY_SECONDS)
        if current_delay is not None:
            schema_dict[vol.Optional(
                CONF_AREA_VENT_OPEN_DELAY_SECONDS,
                default=current_delay,
            )] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=300,
                    step=5,
                    unit_of_measurement="seconds",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        else:
            schema_dict[vol.Optional(
                CONF_AREA_VENT_OPEN_DELAY_SECONDS,
            )] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=300,
                    step=5,
                    unit_of_measurement="seconds",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        data_schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="area_config",
            data_schema=data_schema,
            description_placeholders={
                "area_name": area_info["name"],
            },
        )

    # Dynamic step handler for area_* steps
    def __getattribute__(self, name: str) -> Any:
        """Handle dynamic area step methods."""
        if name.startswith("async_step_area_") and name != "async_step_area_config":
            area_id = name[16:]  # Remove "async_step_area_" prefix

            async def area_step_handler(
                user_input: dict[str, Any] | None = None,
            ) -> config_entries.ConfigFlowResult:
                self._selected_area_id = area_id
                return await self.async_step_area_config(user_input)

            return area_step_handler

        return super().__getattribute__(name)
