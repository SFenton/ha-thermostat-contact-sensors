"""Config flow for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_NOTIFICATION_TAG,
    CONF_NOTIFY_MESSAGE_PAUSED,
    CONF_NOTIFY_MESSAGE_RESUMED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE_PAUSED,
    CONF_NOTIFY_TITLE_RESUMED,
    CONF_OPEN_TIMEOUT,
    CONF_THERMOSTAT,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_NOTIFICATION_TAG,
    DEFAULT_NOTIFY_MESSAGE_PAUSED,
    DEFAULT_NOTIFY_MESSAGE_RESUMED,
    DEFAULT_NOTIFY_TITLE_PAUSED,
    DEFAULT_NOTIFY_TITLE_RESUMED,
    DEFAULT_OPEN_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ThermostatContactSensorsConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Thermostat Contact Sensors."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate inputs
            if not user_input.get(CONF_CONTACT_SENSORS):
                errors[CONF_CONTACT_SENSORS] = "no_sensors_selected"
            elif not user_input.get(CONF_THERMOSTAT):
                errors[CONF_THERMOSTAT] = "no_thermostat_selected"
            else:
                # Create a unique ID based on the thermostat
                await self.async_set_unique_id(user_input[CONF_THERMOSTAT])
                self._abort_if_unique_id_configured()

                name = user_input.get(CONF_NAME, "Thermostat Contact Sensors")

                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_NAME: name,
                        CONF_CONTACT_SENSORS: user_input[CONF_CONTACT_SENSORS],
                        CONF_THERMOSTAT: user_input[CONF_THERMOSTAT],
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
                vol.Required(CONF_CONTACT_SENSORS): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=BINARY_SENSOR_DOMAIN,
                        device_class=["door", "window", "opening", "garage_door"],
                        multiple=True,
                    )
                ),
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
        return ThermostatContactSensorsOptionsFlow()


class ThermostatContactSensorsOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Thermostat Contact Sensors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options

        data_schema = vol.Schema(
            {
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
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )
