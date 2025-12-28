"""Coordinator for Thermostat Contact Sensors integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.const import STATE_ON, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_GRACE_PERIOD_MINUTES,
    CONF_MIN_CYCLE_OFF_MINUTES,
    CONF_MIN_CYCLE_ON_MINUTES,
    CONF_MIN_OCCUPANCY_MINUTES,
    CONF_NOTIFICATION_TAG,
    CONF_NOTIFY_MESSAGE_PAUSED,
    CONF_NOTIFY_MESSAGE_RESUMED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE_PAUSED,
    CONF_NOTIFY_TITLE_RESUMED,
    CONF_OPEN_TIMEOUT,
    CONF_TEMPERATURE_DEADBAND,
    CONF_TEMPERATURE_SENSORS,
    CONF_THERMOSTAT,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_MIN_CYCLE_OFF_MINUTES,
    DEFAULT_MIN_CYCLE_ON_MINUTES,
    DEFAULT_MIN_OCCUPANCY_MINUTES,
    DEFAULT_NOTIFICATION_TAG,
    DEFAULT_NOTIFY_MESSAGE_PAUSED,
    DEFAULT_NOTIFY_MESSAGE_RESUMED,
    DEFAULT_NOTIFY_TITLE_PAUSED,
    DEFAULT_NOTIFY_TITLE_RESUMED,
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_TEMPERATURE_DEADBAND,
    DOMAIN,
)
from .occupancy import RoomOccupancyTracker
from .thermostat_control import ThermostatController, ThermostatState

_LOGGER = logging.getLogger(__name__)


class ThermostatContactSensorsCoordinator(DataUpdateCoordinator):
    """Coordinator to manage thermostat contact sensor logic."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry_id: str,
        contact_sensors: list[str],
        thermostat: str,
        options: dict[str, Any],
        areas_config: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # We use event-based updates
        )
        self.config_entry_id = config_entry_id
        self.contact_sensors = contact_sensors
        self.thermostat = thermostat
        self._areas_config = areas_config or {}
        self._options = options

        # State tracking
        self.is_paused = False
        self.previous_hvac_mode: str | None = None
        self.open_sensors: list[str] = []
        self.trigger_sensor: str | None = None

        # Timeout tracking
        self._open_timer: asyncio.TimerHandle | None = None
        self._close_timer: asyncio.TimerHandle | None = None
        self._pending_open_sensor: str | None = None

        # Track last known non-off HVAC mode for manual override detection
        self._last_known_hvac_mode: str | None = None

        # Listener cleanup
        self._unsub_state_change: callable | None = None
        self._unsub_thermostat_state_change: callable | None = None

        # Occupancy tracker
        min_occupancy = self._options.get(
            CONF_MIN_OCCUPANCY_MINUTES, DEFAULT_MIN_OCCUPANCY_MINUTES
        )
        grace_period = self._options.get(
            CONF_GRACE_PERIOD_MINUTES, DEFAULT_GRACE_PERIOD_MINUTES
        )
        self.occupancy_tracker = RoomOccupancyTracker(
            hass=hass,
            areas_config=self._areas_config,
            min_occupancy_minutes=min_occupancy,
            grace_period_minutes=grace_period,
        )

        # Thermostat controller
        self.thermostat_controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=thermostat,
            occupancy_tracker=self.occupancy_tracker,
            temperature_deadband=self._options.get(
                CONF_TEMPERATURE_DEADBAND, DEFAULT_TEMPERATURE_DEADBAND
            ),
            min_cycle_on_minutes=self._options.get(
                CONF_MIN_CYCLE_ON_MINUTES, DEFAULT_MIN_CYCLE_ON_MINUTES
            ),
            min_cycle_off_minutes=self._options.get(
                CONF_MIN_CYCLE_OFF_MINUTES, DEFAULT_MIN_CYCLE_OFF_MINUTES
            ),
        )

        # Last thermostat state for sensors
        self._last_thermostat_state: ThermostatState | None = None

    @property
    def open_timeout(self) -> int:
        """Return open timeout in minutes."""
        return self._options.get(CONF_OPEN_TIMEOUT, DEFAULT_OPEN_TIMEOUT)

    @property
    def close_timeout(self) -> int:
        """Return close timeout in minutes."""
        return self._options.get(CONF_CLOSE_TIMEOUT, DEFAULT_CLOSE_TIMEOUT)

    @property
    def notify_service(self) -> str:
        """Return notification service."""
        return self._options.get(CONF_NOTIFY_SERVICE, "")

    @property
    def open_count(self) -> int:
        """Return count of open sensors."""
        return len(self.open_sensors)

    @property
    def open_doors_count(self) -> int:
        """Return count of open door sensors."""
        return len([s for s in self.open_sensors if "door" in s.lower()])

    @property
    def open_windows_count(self) -> int:
        """Return count of open window sensors."""
        return len([s for s in self.open_sensors if "window" in s.lower()])

    @property
    def areas_config(self) -> dict[str, dict[str, Any]]:
        """Return the areas configuration."""
        return self._areas_config

    @property
    def last_thermostat_state(self) -> ThermostatState | None:
        """Return the last evaluated thermostat state."""
        return self._last_thermostat_state

    def get_area_temp_sensors(self) -> dict[str, list[str]]:
        """Get temperature sensors for each area.

        Returns:
            Dict of area_id -> list of temperature sensor entity IDs.
        """
        result = {}
        for area_id, area_config in self._areas_config.items():
            temp_sensors = area_config.get(CONF_TEMPERATURE_SENSORS, [])
            if temp_sensors:
                result[area_id] = list(temp_sensors)
        return result

    def update_thermostat_state(self) -> ThermostatState | None:
        """Evaluate and update the current thermostat control state.

        Returns:
            The updated ThermostatState.
        """
        # Get active areas from occupancy tracker
        active_areas = self.occupancy_tracker.active_areas
        area_temp_sensors = self.get_area_temp_sensors()

        # Update pause state on thermostat controller
        self.thermostat_controller.set_paused_by_contact_sensors(self.is_paused)

        # Evaluate what action should be taken
        self._last_thermostat_state = self.thermostat_controller.evaluate_thermostat_action(
            active_areas=active_areas,
            area_temp_sensors=area_temp_sensors,
        )

        return self._last_thermostat_state

    def update_options(self, options: dict[str, Any]) -> None:
        """Update options from config entry."""
        self._options = options

        # Update occupancy tracker
        self.occupancy_tracker.min_occupancy_minutes = options.get(
            CONF_MIN_OCCUPANCY_MINUTES, DEFAULT_MIN_OCCUPANCY_MINUTES
        )

        # Update thermostat controller
        self.thermostat_controller.temperature_deadband = options.get(
            CONF_TEMPERATURE_DEADBAND, DEFAULT_TEMPERATURE_DEADBAND
        )
        self.thermostat_controller.min_cycle_on_minutes = options.get(
            CONF_MIN_CYCLE_ON_MINUTES, DEFAULT_MIN_CYCLE_ON_MINUTES
        )
        self.thermostat_controller.min_cycle_off_minutes = options.get(
            CONF_MIN_CYCLE_OFF_MINUTES, DEFAULT_MIN_CYCLE_OFF_MINUTES
        )

    async def async_setup(self) -> None:
        """Set up the coordinator and start listening to state changes."""
        # Initial scan of sensor states
        self._update_open_sensors()

        # Initialize last known HVAC mode from current thermostat state
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state and climate_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, HVACMode.OFF):
            self._last_known_hvac_mode = climate_state.state

        # Set up occupancy tracker
        await self.occupancy_tracker.async_setup()

        # Register callback for occupancy changes to trigger coordinator updates
        self.occupancy_tracker.register_update_callback(
            lambda: self.hass.async_create_task(self._async_occupancy_changed())
        )

        # Subscribe to contact sensor state changes
        self._unsub_state_change = async_track_state_change_event(
            self.hass,
            self.contact_sensors,
            self._async_sensor_state_changed,
        )

        # Subscribe to thermostat state changes to detect manual overrides
        self._unsub_thermostat_state_change = async_track_state_change_event(
            self.hass,
            [self.thermostat],
            self._async_thermostat_state_changed,
        )

        _LOGGER.debug(
            "Coordinator setup complete. Monitoring %d sensors for thermostat %s",
            len(self.contact_sensors),
            self.thermostat,
        )

        # Initial thermostat state evaluation
        self.update_thermostat_state()

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        self._cancel_open_timer()
        self._cancel_close_timer()

        if self._unsub_state_change:
            self._unsub_state_change()
            self._unsub_state_change = None

        if self._unsub_thermostat_state_change:
            self._unsub_thermostat_state_change()
            self._unsub_thermostat_state_change = None

        # Shut down occupancy tracker
        await self.occupancy_tracker.async_shutdown()

    async def _async_occupancy_changed(self) -> None:
        """Handle occupancy state changes."""
        _LOGGER.debug("Occupancy changed, updating thermostat state")
        self.update_thermostat_state()
        self.async_set_updated_data(None)

    def _update_open_sensors(self) -> None:
        """Update the list of currently open sensors."""
        self.open_sensors = []
        for sensor in self.contact_sensors:
            state = self.hass.states.get(sensor)
            if state and state.state == STATE_ON:
                self.open_sensors.append(sensor)

    def _cancel_open_timer(self) -> None:
        """Cancel the open timeout timer."""
        if self._open_timer:
            self._open_timer.cancel()
            self._open_timer = None
            self._pending_open_sensor = None

    def _cancel_close_timer(self) -> None:
        """Cancel the close timeout timer."""
        if self._close_timer:
            self._close_timer.cancel()
            self._close_timer = None

    @callback
    def _async_thermostat_state_changed(self, event) -> None:
        """Handle thermostat state changes to detect manual overrides."""
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")

        if new_state is None:
            return

        # Ignore unavailable/unknown states
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(
            "Thermostat %s changed from %s to %s (is_paused=%s)",
            self.thermostat,
            old_state.state if old_state else "None",
            new_state.state,
            self.is_paused,
        )

        # Track the last non-off HVAC mode
        if new_state.state != HVACMode.OFF:
            self._last_known_hvac_mode = new_state.state
            _LOGGER.debug("Updated last known HVAC mode to: %s", self._last_known_hvac_mode)

        # Handle manual overrides while paused
        if self.is_paused:
            if new_state.state != HVACMode.OFF:
                # User manually turned thermostat back on - respect their choice
                _LOGGER.info(
                    "User manually turned thermostat on to %s while paused. Respecting override.",
                    new_state.state,
                )
                self.is_paused = False
                self.previous_hvac_mode = None
                self.trigger_sensor = None
                self._cancel_close_timer()
                self.async_set_updated_data(None)
            elif old_state and old_state.state != HVACMode.OFF:
                # User manually turned thermostat off (it was on from their override)
                # Update previous_hvac_mode to their last choice so we restore correctly
                _LOGGER.debug(
                    "User turned thermostat off while sensors open. Will restore to: %s",
                    self._last_known_hvac_mode,
                )
                if self._last_known_hvac_mode:
                    self.previous_hvac_mode = self._last_known_hvac_mode

    @callback
    def _async_sensor_state_changed(self, event) -> None:
        """Handle sensor state changes."""
        entity_id = event.data.get("entity_id")
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")

        if new_state is None:
            return

        # Ignore unavailable/unknown states
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(
            "Sensor %s changed from %s to %s",
            entity_id,
            old_state.state if old_state else "None",
            new_state.state,
        )

        # Update open sensors list
        self._update_open_sensors()

        # Handle sensor opening
        if new_state.state == STATE_ON and (old_state is None or old_state.state == STATE_OFF):
            self._handle_sensor_opened(entity_id)

        # Handle sensor closing
        elif new_state.state == STATE_OFF and old_state and old_state.state == STATE_ON:
            self._handle_sensor_closed(entity_id)

        # Notify listeners of data update
        self.async_set_updated_data(None)

    def _handle_sensor_opened(self, entity_id: str) -> None:
        """Handle a sensor being opened."""
        _LOGGER.debug("Sensor opened: %s", entity_id)

        # Cancel any close timer since something opened
        self._cancel_close_timer()

        # If already paused, nothing more to do
        if self.is_paused:
            return

        # If no open timer running, start one
        if self._open_timer is None:
            self._pending_open_sensor = entity_id
            self._open_timer = self.hass.loop.call_later(
                self.open_timeout * 60,
                lambda: self.hass.async_create_task(self._async_open_timeout_expired()),
            )
            _LOGGER.debug(
                "Started open timer for %d minutes (triggered by %s)",
                self.open_timeout,
                entity_id,
            )

    def _handle_sensor_closed(self, entity_id: str) -> None:
        """Handle a sensor being closed."""
        _LOGGER.debug("Sensor closed: %s", entity_id)

        # If this was the pending sensor and no others are open, cancel the timer
        if not self.is_paused:
            if self._pending_open_sensor == entity_id and len(self.open_sensors) == 0:
                self._cancel_open_timer()
                _LOGGER.debug("Cancelled open timer - sensor closed before timeout")
            return

        # If paused and all sensors are now closed, start close timer
        if self.is_paused and len(self.open_sensors) == 0:
            if self._close_timer is None:
                self._close_timer = self.hass.loop.call_later(
                    self.close_timeout * 60,
                    lambda: self.hass.async_create_task(self._async_close_timeout_expired()),
                )
                _LOGGER.debug(
                    "Started close timer for %d minutes",
                    self.close_timeout,
                )

    async def _async_open_timeout_expired(self) -> None:
        """Handle open timeout expiration - pause the thermostat."""
        self._open_timer = None

        # Check if sensors are still open
        self._update_open_sensors()
        if len(self.open_sensors) == 0:
            _LOGGER.debug("Open timeout expired but all sensors are closed")
            return

        _LOGGER.info(
            "Open timeout expired with %d sensors open. Pausing thermostat.",
            len(self.open_sensors),
        )

        # Store the trigger sensor for notifications
        self.trigger_sensor = self._pending_open_sensor
        self._pending_open_sensor = None

        # Get current HVAC mode before turning off
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state:
            self.previous_hvac_mode = climate_state.state
        else:
            self.previous_hvac_mode = HVACMode.AUTO

        # Turn off the thermostat
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_hvac_mode",
            {
                "entity_id": self.thermostat,
                "hvac_mode": HVACMode.OFF,
            },
            blocking=True,
        )

        self.is_paused = True

        # Send notification
        await self._async_send_notification(paused=True)

        # Notify listeners
        self.async_set_updated_data(None)

        _LOGGER.info("Thermostat paused. Previous mode: %s", self.previous_hvac_mode)

    async def _async_close_timeout_expired(self) -> None:
        """Handle close timeout expiration - resume the thermostat."""
        self._close_timer = None

        # Double-check all sensors are still closed
        self._update_open_sensors()
        if len(self.open_sensors) > 0:
            _LOGGER.debug(
                "Close timeout expired but %d sensors are still open",
                len(self.open_sensors),
            )
            return

        _LOGGER.info(
            "Close timeout expired with all sensors closed. Resuming thermostat."
        )

        # Restore previous HVAC mode
        if self.previous_hvac_mode and self.previous_hvac_mode != HVACMode.OFF:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                "set_hvac_mode",
                {
                    "entity_id": self.thermostat,
                    "hvac_mode": self.previous_hvac_mode,
                },
                blocking=True,
            )

        # Send notification
        await self._async_send_notification(paused=False)

        self.is_paused = False
        self.trigger_sensor = None

        # Notify listeners
        self.async_set_updated_data(None)

        _LOGGER.info("Thermostat resumed to mode: %s", self.previous_hvac_mode)

    async def _async_send_notification(self, paused: bool) -> None:
        """Send a notification about thermostat state change."""
        notify_service = self.notify_service
        if not notify_service:
            return

        # Parse the service name
        if "." in notify_service:
            domain, service = notify_service.split(".", 1)
        else:
            domain = "notify"
            service = notify_service

        # Build template context
        trigger_sensor_name = "A sensor"
        if self.trigger_sensor:
            state = self.hass.states.get(self.trigger_sensor)
            if state:
                trigger_sensor_name = state.attributes.get(
                    "friendly_name", self.trigger_sensor
                )

        open_sensor_names = []
        for sensor in self.open_sensors:
            state = self.hass.states.get(sensor)
            if state:
                open_sensor_names.append(
                    state.attributes.get("friendly_name", sensor)
                )

        # Get thermostat friendly name
        thermostat_name = self.thermostat
        thermostat_state = self.hass.states.get(self.thermostat)
        if thermostat_state:
            thermostat_name = thermostat_state.attributes.get(
                "friendly_name", self.thermostat
            )

        template_vars = {
            "trigger_sensor": self.trigger_sensor or "",
            "trigger_sensor_name": trigger_sensor_name,
            "open_sensors": self.open_sensors,
            "open_sensor_names": open_sensor_names,
            "open_count": self.open_count,
            "open_doors": self.open_doors_count,
            "open_windows": self.open_windows_count,
            "open_timeout": self.open_timeout,
            "close_timeout": self.close_timeout,
            "previous_mode": self.previous_hvac_mode or "unknown",
            "thermostat": self.thermostat,
            "thermostat_name": thermostat_name,
        }

        if paused:
            title_template = self._options.get(
                CONF_NOTIFY_TITLE_PAUSED, DEFAULT_NOTIFY_TITLE_PAUSED
            )
            message_template = self._options.get(
                CONF_NOTIFY_MESSAGE_PAUSED, DEFAULT_NOTIFY_MESSAGE_PAUSED
            )
        else:
            title_template = self._options.get(
                CONF_NOTIFY_TITLE_RESUMED, DEFAULT_NOTIFY_TITLE_RESUMED
            )
            message_template = self._options.get(
                CONF_NOTIFY_MESSAGE_RESUMED, DEFAULT_NOTIFY_MESSAGE_RESUMED
            )

        # Render templates
        title = await self._async_render_template(title_template, template_vars)
        message = await self._async_render_template(message_template, template_vars)

        notification_tag = self._options.get(
            CONF_NOTIFICATION_TAG, DEFAULT_NOTIFICATION_TAG
        )

        try:
            await self.hass.services.async_call(
                domain,
                service,
                {
                    "title": title,
                    "message": message,
                    "data": {
                        "tag": notification_tag,
                    },
                },
                blocking=True,
            )
            _LOGGER.debug("Notification sent: %s", title)
        except Exception as ex:
            _LOGGER.error("Failed to send notification: %s", ex)

    async def _async_render_template(
        self, template_str: str, variables: dict[str, Any]
    ) -> str:
        """Render a template string with variables."""
        try:
            template = Template(template_str, self.hass)
            return template.async_render(variables)
        except Exception as ex:
            _LOGGER.error("Failed to render template: %s", ex)
            return template_str

    async def _async_update_data(self) -> None:
        """Update data - not used as we're event-driven."""
        return None
