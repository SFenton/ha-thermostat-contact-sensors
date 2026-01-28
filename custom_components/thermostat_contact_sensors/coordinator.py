"""Coordinator for Thermostat Contact Sensors integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.const import (
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_ENABLED,
    CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
    CONF_AREA_VENT_OPEN_DELAY_SECONDS,
    CONF_AWAY_COOL_TEMP_DIFF,
    CONF_AWAY_HEAT_TEMP_DIFF,
    CONF_AWAY_PRESENCE_ENTITY,
    CONF_CLOSE_TIMEOUT,
    CONF_ECO_MODE_CRITICAL_TRACKING,
    CONF_GRACE_PERIOD_MINUTES,
    CONF_COOLING_BOOST_OFFSET,
    CONF_HEATING_BOOST_OFFSET,
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
    CONF_TEMPERATURE_DEADBAND,
    CONF_TEMPERATURE_SENSORS,
    CONF_UNOCCUPIED_COOLING_THRESHOLD,
    CONF_UNOCCUPIED_HEATING_THRESHOLD,
    CONF_VENT_DEBOUNCE_SECONDS,
    CONF_VENT_OPEN_DELAY_SECONDS,
    CONF_VENTS,
    DEFAULT_AWAY_COOL_TEMP_DIFF,
    DEFAULT_AWAY_HEAT_TEMP_DIFF,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_COOLING_BOOST_OFFSET,
    DEFAULT_ECO_MODE_CRITICAL_TRACKING,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_HEATING_BOOST_OFFSET,
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
    ECO_CRITICAL_ALL,
    ECO_CRITICAL_NONE,
    ECO_CRITICAL_SELECT,
)
from .occupancy import RoomOccupancyTracker
from .thermostat_control import ThermostatController, ThermostatState
from .vent_control import VentController, VentControlState

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
        # When True, the integration will not run any automation (timers, pause/resume,
        # thermostat/vent recalculation). Used by pause_integration/resume_integration services.
        self.integration_paused: bool = False
        self.previous_hvac_mode: str | None = None
        # Dict of entity_id -> timestamp when sensor opened
        self._open_sensor_times: dict[str, float] = {}
        self.trigger_sensor: str | None = None
        self.respect_user_off: bool = False  # Default: always resume thermostat

        # Tracked rooms feature: only heat/cool selected rooms.
        # When disabled, all rooms are considered.
        self.only_track_selected_rooms: bool = False
        self._tracked_rooms: set[str] = set()

        # Timeout tracking
        self._open_timer: asyncio.TimerHandle | None = None
        self._close_timer: asyncio.TimerHandle | None = None
        self._pending_open_sensor: str | None = None

        # Track last known non-off HVAC mode for manual override detection
        self._last_known_hvac_mode: str | None = None

        # Listener cleanup
        self._unsub_state_change: callable | None = None
        self._unsub_thermostat_state_change: callable | None = None
        self._unsub_temp_sensor_state_change: callable | None = None
        self._unsub_presence_state_change: callable | None = None

        # Away mode tracking
        self._is_away: bool = False

        # Eco away behavior: controls eco mode behavior when away.
        # Values are defined in select.EcoAwayBehavior.
        self.eco_away_behavior: str = "disable_eco_when_away"

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
            entry_id=config_entry_id,
        )

        # Thermostat controller
        self.thermostat_controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=thermostat,
            occupancy_tracker=self.occupancy_tracker,
            entry_id=config_entry_id,
            temperature_deadband=self._options.get(
                CONF_TEMPERATURE_DEADBAND, DEFAULT_TEMPERATURE_DEADBAND
            ),
            min_cycle_on_minutes=self._options.get(
                CONF_MIN_CYCLE_ON_MINUTES, DEFAULT_MIN_CYCLE_ON_MINUTES
            ),
            min_cycle_off_minutes=self._options.get(
                CONF_MIN_CYCLE_OFF_MINUTES, DEFAULT_MIN_CYCLE_OFF_MINUTES
            ),
            unoccupied_heating_threshold=self._options.get(
                CONF_UNOCCUPIED_HEATING_THRESHOLD, DEFAULT_UNOCCUPIED_HEATING_THRESHOLD
            ),
            unoccupied_cooling_threshold=self._options.get(
                CONF_UNOCCUPIED_COOLING_THRESHOLD, DEFAULT_UNOCCUPIED_COOLING_THRESHOLD
            ),
            heating_boost_offset=self._options.get(
                CONF_HEATING_BOOST_OFFSET, DEFAULT_HEATING_BOOST_OFFSET
            ),
            cooling_boost_offset=self._options.get(
                CONF_COOLING_BOOST_OFFSET, DEFAULT_COOLING_BOOST_OFFSET
            ),
            area_thermostats_getter=lambda: getattr(self, "area_thermostats", {}),
            global_thermostat_getter=lambda: getattr(self, "global_thermostat", None),
        )

        # Vent controller
        self.vent_controller = VentController(
            hass=hass,
            min_vents_open=self._options.get(
                CONF_MIN_VENTS_OPEN, DEFAULT_MIN_VENTS_OPEN
            ),
            vent_open_delay_seconds=self._options.get(
                CONF_VENT_OPEN_DELAY_SECONDS, DEFAULT_VENT_OPEN_DELAY_SECONDS
            ),
            vent_debounce_seconds=self._options.get(
                CONF_VENT_DEBOUNCE_SECONDS, DEFAULT_VENT_DEBOUNCE_SECONDS
            ),
        )

        # Last vent control state
        self._last_vent_control_state: VentControlState | None = None

        # Last thermostat state for sensors
        self._last_thermostat_state: ThermostatState | None = None

        # Eco Mode Critical Tracking - will be set by Select entity restore or default
        self.eco_mode_critical_tracking: str = self._options.get(
            CONF_ECO_MODE_CRITICAL_TRACKING,
            DEFAULT_ECO_MODE_CRITICAL_TRACKING,
        )

        # Climate platform populates these, but tests and controllers expect them to exist.
        self.area_thermostats: dict[str, Any] = {}
        self.global_thermostat: Any | None = None

        # Eco Mode enabled/disabled (boolean). The select controls how eco behaves
        # for inactive critical rooms, but does not toggle eco itself.
        self._eco_mode_enabled: bool = False

    @property
    def eco_mode(self) -> bool:
        """Return True if eco mode is enabled."""
        return self._eco_mode_enabled

    @eco_mode.setter
    def eco_mode(self, value: bool) -> None:
        """Enable/disable eco mode (boolean)."""
        self._eco_mode_enabled = bool(value)

    @property
    def tracked_rooms(self) -> set[str]:
        """Return the set of tracked room/area IDs."""
        return set(self._tracked_rooms)

    def set_room_tracked(self, area_id: str, tracked: bool) -> None:
        """Add or remove a room from the tracked set."""
        if tracked:
            self._tracked_rooms.add(area_id)
        else:
            self._tracked_rooms.discard(area_id)

    def is_room_tracked(self, area_id: str) -> bool:
        """Return True if a room is currently tracked."""
        if not self.only_track_selected_rooms:
            # When the feature is disabled, all rooms are effectively tracked.
            return True
        return area_id in self._tracked_rooms

    @property
    def all_enabled_area_ids(self) -> set[str]:
        """Return all enabled area IDs."""
        enabled: set[str] = set()
        for area_id, area_config in self._areas_config.items():
            if area_config.get(CONF_AREA_ENABLED, True):
                enabled.add(area_id)
        return enabled

    @property
    def away_presence_entity(self) -> str:
        """Return the presence entity for away mode detection."""
        return self._options.get(CONF_AWAY_PRESENCE_ENTITY, "")

    @property
    def away_heat_temp_diff(self) -> float:
        """Return the heat temperature adjustment when away."""
        return self._options.get(CONF_AWAY_HEAT_TEMP_DIFF, DEFAULT_AWAY_HEAT_TEMP_DIFF)

    @property
    def away_cool_temp_diff(self) -> float:
        """Return the cool temperature adjustment when away."""
        return self._options.get(CONF_AWAY_COOL_TEMP_DIFF, DEFAULT_AWAY_COOL_TEMP_DIFF)

    @property
    def is_away(self) -> bool:
        """Return whether away mode is currently active."""
        return self._is_away

    @property
    def away_mode_configured(self) -> bool:
        """Return whether away mode has been configured with a presence entity."""
        return bool(self.away_presence_entity)

    def get_physical_thermostat_hvac_action(self):
        """Return hvac_action of the physical thermostat if available."""
        from homeassistant.components.climate import HVACAction

        state = self.hass.states.get(self.thermostat)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        hvac_action = state.attributes.get("hvac_action")
        if hvac_action is None:
            return None
        try:
            return HVACAction(hvac_action)
        except ValueError:
            return None

    def _check_presence_entity_state(self) -> bool:
        """Check if the presence entity indicates 'away' state."""
        entity_id = self.away_presence_entity
        if not entity_id:
            return False

        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False

        state_value = state.state.lower()
        return state_value in (STATE_NOT_HOME, STATE_OFF, "false", "away")

    @callback
    def _async_presence_state_changed(self, event) -> None:
        """Handle presence entity state changes."""
        new_state: State | None = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        was_away = self._is_away
        self._is_away = self._check_presence_entity_state()

        if was_away != self._is_away:
            _LOGGER.info("Away mode changed: is_away=%s", self._is_away)
            self.hass.async_create_task(self._async_occupancy_changed())

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
    def open_sensors(self) -> list[str]:
        """Return list of currently open sensors (for backwards compatibility)."""
        return list(self._open_sensor_times.keys())

    @property
    def open_count(self) -> int:
        """Return count of open sensors."""
        return len(self._open_sensor_times)

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

    @property
    def last_vent_control_state(self) -> VentControlState | None:
        """Return the last evaluated vent control state."""
        return self._last_vent_control_state

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

    def get_area_vents(self) -> dict[str, list[str]]:
        """Get vents for each area.

        Returns:
            Dict of area_id -> list of vent entity IDs.
        """
        result = {}
        for area_id, area_config in self._areas_config.items():
            vents = area_config.get(CONF_VENTS, [])
            if vents:
                result[area_id] = list(vents)
        return result

    def get_area_vent_delays(self) -> dict[str, int]:
        """Get per-area vent open delay overrides.

        Returns:
            Dict of area_id -> delay in seconds (only for areas with overrides).
        """
        result = {}
        for area_id, area_config in self._areas_config.items():
            delay = area_config.get(CONF_AREA_VENT_OPEN_DELAY_SECONDS)
            if delay is not None:
                result[area_id] = delay
        return result

    def _area_has_critical_override(self, area_id: str) -> bool:
        """Check if an area has the force_track_when_critical override enabled.

        Args:
            area_id: The area ID to check.

        Returns:
            True if the area should always be checked for critical temperatures.
        """
        area_config = self._areas_config.get(area_id, {})
        return area_config.get(CONF_AREA_FORCE_TRACK_WHEN_CRITICAL, False)

    def update_thermostat_state(self) -> ThermostatState | None:
        """Evaluate and update the current thermostat control state.

        Returns:
            The updated ThermostatState.
        """
        # Get active and inactive areas from occupancy tracker
        all_active_areas = self.occupancy_tracker.active_areas
        all_inactive_areas = self.occupancy_tracker.inactive_areas
        area_temp_sensors = self.get_area_temp_sensors()

        # TSR affects thermostat *decision-making*, not whether we evaluate a room.
        # We always evaluate all active areas for temperature state (for visibility
        # and vent control), but we only *count* tracked areas for thermostat actions.
        active_areas = all_active_areas
        tracked_area_ids: set[str] | None = (
            set(self._tracked_rooms) if self.only_track_selected_rooms else None
        )

        force_critical_area_ids = {
            area_id
            for area_id in self._areas_config.keys()
            if self._area_has_critical_override(area_id)
        }

        # Apply eco-away behavior when everyone is away.
        # Critical tracking policy is only meaningful when eco is enabled.
        eco_mode_for_thermostat = self.eco_mode
        effective_eco_critical_tracking = (
            self.eco_mode_critical_tracking
            if eco_mode_for_thermostat
            else ECO_CRITICAL_ALL
        )
        eco_away_targets: tuple[float, float] | None = None

        if self.away_mode_configured and self.is_away and eco_mode_for_thermostat:
            if self.eco_away_behavior in (
                "disable_eco_when_away",
                "use_eco_away_targets",
            ):
                eco_mode_for_thermostat = False
                effective_eco_critical_tracking = ECO_CRITICAL_ALL

            if self.eco_away_behavior == "use_eco_away_targets":
                eco_away_thermostat = getattr(self, "eco_away_thermostat", None)
                if eco_away_thermostat is not None:
                    eco_away_targets = (
                        eco_away_thermostat.effective_target_temp_low,
                        eco_away_thermostat.effective_target_temp_high,
                    )

        # Filter inactive areas based on Eco Mode Critical Tracking setting and per-area overrides
        # Three options for how to handle inactive critical rooms:
        # 1. ECO_CRITICAL_NONE = ignore all inactive rooms (original Eco Mode ON behavior)
        # 2. ECO_CRITICAL_SELECT = only track rooms with force_track_when_critical override
        #    OR rooms in the tracked rooms list (when TSR is enabled)
        # 3. ECO_CRITICAL_ALL = track all inactive critical rooms (original Eco Mode OFF behavior)
        #
        # Note: TSR filtering of active rooms is no longer applied here. Per-area
        # force_track_when_critical is still respected for *inactive* rooms.

        if not eco_mode_for_thermostat:
            # When eco mode is off, apply TSR filtering if enabled
            if self.only_track_selected_rooms:
                inactive_areas = [
                    area
                    for area in all_inactive_areas
                    if self.is_room_tracked(area.area_id) or self._area_has_critical_override(area.area_id)
                ]
            else:
                inactive_areas = all_inactive_areas
        elif effective_eco_critical_tracking == ECO_CRITICAL_NONE:
            # Even with ECO_CRITICAL_NONE, respect per-area FTCR overrides
            inactive_areas = [
                area
                for area in all_inactive_areas
                if self._area_has_critical_override(area.area_id)
            ]
        elif effective_eco_critical_tracking == ECO_CRITICAL_SELECT:
            inactive_areas = [
                area
                for area in all_inactive_areas
                if self._area_has_critical_override(area.area_id)
                or (self.only_track_selected_rooms and self.is_room_tracked(area.area_id))
            ]
        else:  # ECO_CRITICAL_ALL
            inactive_areas = all_inactive_areas

        # No longer add TSR-filtered active areas to inactive_areas: all active areas
        # are evaluated directly as active.

        # Update pause state on thermostat controller
        self.thermostat_controller.set_paused_by_contact_sensors(self.is_paused)

        # Evaluate what action should be taken
        self._last_thermostat_state = self.thermostat_controller.evaluate_thermostat_action(
            active_areas=active_areas,
            area_temp_sensors=area_temp_sensors,
            inactive_areas=inactive_areas,
            respect_user_off=self.respect_user_off,
            eco_mode=eco_mode_for_thermostat,
            eco_away_targets=eco_away_targets,
            # Trend/inferred HVAC mode should always be based on *all* rooms' sensors,
            # independent of Eco/TSR/force-critical filtering.
            all_areas_for_trend=list(self.occupancy_tracker.areas.values()),
            tracked_area_ids=tracked_area_ids,
            force_critical_area_ids=force_critical_area_ids,
        )

        return self._last_thermostat_state

    async def async_update_thermostat_state(self) -> ThermostatState | None:
        """Evaluate, update, and execute thermostat control actions.

        This is the async version that also executes the recommended action.

        Returns:
            The updated ThermostatState.
        """
        if self.integration_paused:
            return self._last_thermostat_state

        # First evaluate the state
        state = self.update_thermostat_state()

        if state is None:
            return None

        # Don't execute actions if paused by contact sensors
        # (the contact sensor logic handles turning off/on)
        if self.is_paused:
            _LOGGER.debug("Skipping thermostat action execution - paused by contact sensors")
            return state

        # Execute the recommended action
        executed = await self.thermostat_controller.async_execute_action(state)
        if executed:
            _LOGGER.debug(
                "Thermostat action executed: %s",
                state.recommended_action.value if state.recommended_action else "none",
            )

        return state

    async def async_update_thermostat_and_vents(self) -> None:
        """Re-evaluate thermostat state and then vents.

        This is used by UI entities (switch/select) where a config toggle should
        take effect immediately for both thermostat and vent control.
        """
        if self.integration_paused:
            return

        await self.async_update_thermostat_state()
        await self.async_update_vents()
        self.async_set_updated_data(None)

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
        self.thermostat_controller.unoccupied_heating_threshold = options.get(
            CONF_UNOCCUPIED_HEATING_THRESHOLD, DEFAULT_UNOCCUPIED_HEATING_THRESHOLD
        )
        self.thermostat_controller.unoccupied_cooling_threshold = options.get(
            CONF_UNOCCUPIED_COOLING_THRESHOLD, DEFAULT_UNOCCUPIED_COOLING_THRESHOLD
        )

        # Update vent controller
        self.vent_controller.min_vents_open = options.get(
            CONF_MIN_VENTS_OPEN, DEFAULT_MIN_VENTS_OPEN
        )
        self.vent_controller.vent_open_delay_seconds = options.get(
            CONF_VENT_OPEN_DELAY_SECONDS, DEFAULT_VENT_OPEN_DELAY_SECONDS
        )
        self.vent_controller.vent_debounce_seconds = options.get(
            CONF_VENT_DEBOUNCE_SECONDS, DEFAULT_VENT_DEBOUNCE_SECONDS
        )

    async def async_setup(self, *, run_initial_actions: bool = False) -> None:
        """Set up the coordinator and start listening to state changes.

        Args:
            run_initial_actions: When True, performs an initial thermostat + vent
                update that may call Home Assistant services. The config-entry setup
                path uses this to initialize state on startup. Unit tests that
                construct the coordinator directly typically leave this False to
                avoid unwanted service side effects.
        """
        # Initial scan of sensor states
        self._update_open_sensors()
        # If sensors are already open on startup, start the timer (unless integration paused).
        self._check_initial_open_sensors()

        # Initialize away state and subscribe to presence entity changes if configured
        self._is_away = self._check_presence_entity_state()
        if self.away_presence_entity:
            self._unsub_presence_state_change = async_track_state_change_event(
                self.hass,
                [self.away_presence_entity],
                self._async_presence_state_changed,
            )

        # Initialize last known HVAC mode from current thermostat state
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state and climate_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, HVACMode.OFF):
            self._last_known_hvac_mode = climate_state.state

        # Set up occupancy tracker
        await self.occupancy_tracker.async_setup()

        # Set up thermostat controller (restores state)
        await self.thermostat_controller.async_setup()

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

        # Subscribe to temperature sensor state changes for vent control updates
        all_temp_sensors = []
        for area_config in self._areas_config.values():
            all_temp_sensors.extend(area_config.get(CONF_TEMPERATURE_SENSORS, []))
        if all_temp_sensors:
            self._unsub_temp_sensor_state_change = async_track_state_change_event(
                self.hass,
                all_temp_sensors,
                self._async_temp_sensor_state_changed,
            )

        _LOGGER.debug(
            "Coordinator setup complete. Monitoring %d sensors for thermostat %s",
            len(self.contact_sensors),
            self.thermostat,
        )

        # Initial evaluation (no service calls)
        self.update_thermostat_state()

        # Optionally run initial actions (may call HA services)
        if run_initial_actions:
            await self.async_update_thermostat_state()
            await self.async_update_vents()

    async def async_pause_integration(self) -> None:
        """Pause the integration - completely stops all automation."""
        if self.integration_paused:
            _LOGGER.info("Integration already paused")
            return

        _LOGGER.info("Pausing integration automation")
        self.integration_paused = True

        # Cancel any pending timers so nothing fires while paused
        self._cancel_open_timer()
        self._cancel_close_timer()

        # Notify listeners
        self.async_set_updated_data(None)

    async def async_resume_integration(self) -> None:
        """Resume the integration - re-enables all automation."""
        if not self.integration_paused:
            _LOGGER.info("Integration not paused")
            return

        _LOGGER.info("Resuming integration automation")
        self.integration_paused = False

        # On resume, check for sensors already open and start timers accordingly
        self._check_initial_open_sensors()

        # Re-evaluate state now that automation is active again
        await self.async_update_thermostat_state()
        await self.async_update_vents()

        # Notify listeners
        self.async_set_updated_data(None)

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

        if self._unsub_temp_sensor_state_change:
            self._unsub_temp_sensor_state_change()
            self._unsub_temp_sensor_state_change = None

        if self._unsub_presence_state_change:
            self._unsub_presence_state_change()
            self._unsub_presence_state_change = None

        # Shut down thermostat controller (saves state)
        await self.thermostat_controller.async_shutdown()

        # Shut down occupancy tracker
        await self.occupancy_tracker.async_shutdown()

    async def _async_occupancy_changed(self) -> None:
        """Handle occupancy state changes."""
        if self.integration_paused:
            _LOGGER.debug("Integration paused, ignoring occupancy change")
            return
        _LOGGER.debug("Occupancy changed, updating thermostat state")
        await self.async_update_thermostat_state()
        await self.async_update_vents()
        self.async_set_updated_data(None)

    @callback
    def _async_temp_sensor_state_changed(self, event) -> None:
        """Handle temperature sensor state changes."""
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")

        if new_state is None:
            return

        # Ignore unavailable/unknown states
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(
            "Temperature sensor %s changed to %s",
            entity_id,
            new_state.state,
        )

        # Update thermostat state and vents (async tasks from callback)
        self.hass.async_create_task(self._async_handle_temp_change())

    async def _async_handle_temp_change(self) -> None:
        """Handle temperature change - evaluate and execute thermostat actions."""
        if self.integration_paused:
            _LOGGER.debug("Integration paused, ignoring temperature change")
            return
        await self.async_update_thermostat_state()
        await self.async_update_vents()
        self.async_set_updated_data(None)

    async def async_update_vents(self) -> VentControlState | None:
        """Evaluate and execute vent control.

        Returns:
            The VentControlState with any pending commands executed.
        """
        if self.integration_paused:
            return self._last_vent_control_state

        area_vents = self.get_area_vents()
        if not area_vents:
            return None

        # Vent control is intentionally independent of TSR.
        # TSR controls which rooms can *drive thermostat actions*, but vents should
        # respond to occupancy/temperature in all rooms to prevent starvation.
        active_areas = self.occupancy_tracker.active_areas
        occupied_areas = self.occupancy_tracker.occupied_areas

        # Get room temperature states and target temperatures from last thermostat state
        room_temp_states = {}
        hvac_mode = None
        target_temp_low = None
        target_temp_high = None
        if self._last_thermostat_state:
            room_temp_states = self._last_thermostat_state.room_states
            hvac_mode = self._last_thermostat_state.hvac_mode
            target_temp_low = self._last_thermostat_state.target_temp_low
            target_temp_high = self._last_thermostat_state.target_temp_high

        # Get per-area vent delay overrides
        area_vent_delays = self.get_area_vent_delays()

        # Evaluate all vents
        control_state = self.vent_controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=active_areas,
            occupied_areas=occupied_areas,
            room_temp_states=room_temp_states,
            area_vent_delays=area_vent_delays,
            hvac_mode=hvac_mode,
            target_temp_low=target_temp_low,
            target_temp_high=target_temp_high,
        )

        # Execute pending commands
        if control_state.pending_commands:
            executed = await self.vent_controller.async_execute_vent_commands(
                control_state
            )
            _LOGGER.debug(
                "Executed %d vent commands out of %d pending",
                executed,
                len(control_state.pending_commands),
            )

        self._last_vent_control_state = control_state
        return control_state

    def _update_open_sensors(self) -> None:
        """Update the dict of currently open sensors with timestamps."""
        current_time = time.monotonic()
        now_utc = dt_util.utcnow()
        new_open_sensors: dict[str, float] = {}
        for sensor in self.contact_sensors:
            state = self.hass.states.get(sensor)
            if state and state.state == STATE_ON:
                # Preserve existing timestamp if sensor was already open
                if sensor in self._open_sensor_times:
                    new_open_sensors[sensor] = self._open_sensor_times[sensor]
                else:
                    # Approximate monotonic open time based on HA state's last_changed,
                    # so that sensors that were opened earlier are treated as earlier.
                    opened_at = getattr(state, "last_changed", None)
                    if opened_at is not None:
                        age_seconds = (now_utc - opened_at).total_seconds()
                        if age_seconds < 0:
                            age_seconds = 0
                        new_open_sensors[sensor] = current_time - age_seconds
                    else:
                        new_open_sensors[sensor] = current_time
        self._open_sensor_times = new_open_sensors

    def _check_initial_open_sensors(self) -> None:
        """Start the open timer for sensors already open on startup/resume."""
        if self.integration_paused:
            return

        if self.is_paused:
            # Already paused by contact sensor; don't start new timers.
            return

        self._update_open_sensors()
        if not self._open_sensor_times:
            return

        # If a timer is already running, leave it alone.
        if self._open_timer is not None:
            return

        # Find earliest still-open sensor and schedule remaining time
        earliest_sensor = min(
            self._open_sensor_times.keys(),
            key=lambda s: self._open_sensor_times[s],
        )
        earliest_time = self._open_sensor_times[earliest_sensor]
        elapsed = time.monotonic() - earliest_time
        remaining = (self.open_timeout * 60) - elapsed

        self._pending_open_sensor = earliest_sensor
        if remaining <= 0:
            self.hass.async_create_task(self._async_open_timeout_expired())
        else:
            self._open_timer = self.hass.loop.call_later(
                remaining,
                lambda: self.hass.async_create_task(self._async_open_timeout_expired()),
            )

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

    def _recalculate_open_timer(self) -> None:
        """Recalculate the open timer based on the earliest still-open sensor.
        
        Called when the original triggering sensor closes but others remain open.
        The new timer should expire when the earliest still-open sensor has been
        open for the full timeout duration.
        """
        if not self._open_sensor_times:
            self._cancel_open_timer()
            return

        # Find the sensor that has been open the longest (earliest timestamp)
        earliest_sensor = min(self._open_sensor_times.keys(), 
                              key=lambda s: self._open_sensor_times[s])
        earliest_time = self._open_sensor_times[earliest_sensor]
        
        # Calculate how much time remains until this sensor hits the timeout
        current_time = time.monotonic()
        elapsed = current_time - earliest_time
        remaining = (self.open_timeout * 60) - elapsed
        
        # Cancel the old timer
        self._cancel_open_timer()
        
        if remaining <= 0:
            # Timer should have already fired - trigger immediately
            _LOGGER.debug(
                "Recalculated timer expired immediately (sensor %s open for %.1f min)",
                earliest_sensor,
                elapsed / 60,
            )
            self._pending_open_sensor = earliest_sensor
            self.hass.async_create_task(self._async_open_timeout_expired())
        else:
            # Schedule new timer for the remaining time
            self._pending_open_sensor = earliest_sensor
            self._open_timer = self.hass.loop.call_later(
                remaining,
                lambda: self.hass.async_create_task(self._async_open_timeout_expired()),
            )
            _LOGGER.debug(
                "Recalculated open timer: %.1f min remaining for sensor %s",
                remaining / 60,
                earliest_sensor,
            )

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
            # Clear the "we turned off" flag since thermostat is now on
            # (either we turned it on, or user did)
            self.thermostat_controller._we_turned_off = False

        # Keep coordinator entities (including vTherms) fresh when the physical thermostat
        # changes state or key attributes like hvac_action.
        old_hvac_action = old_state.attributes.get("hvac_action") if old_state else None
        new_hvac_action = new_state.attributes.get("hvac_action")
        if old_state is None or old_state.state != new_state.state or old_hvac_action != new_hvac_action:
            self.async_set_updated_data(None)

        # Handle manual overrides while paused
        if self.is_paused:
            # Only treat an OFF -> ON mode transition as a manual override.
            # Attribute-only changes (e.g., fan_mode updates) should not clear the paused state.
            if (
                old_state
                and old_state.state == HVACMode.OFF
                and new_state.state != HVACMode.OFF
            ):
                _LOGGER.info(
                    "User manually turned thermostat on to %s while paused. Respecting override.",
                    new_state.state,
                )
                self.is_paused = False
                self.previous_hvac_mode = None
                self.trigger_sensor = None
                self._cancel_close_timer()
                self.async_set_updated_data(None)

    @callback
    def _async_sensor_state_changed(self, event) -> None:
        """Handle sensor state changes."""
        if self.integration_paused:
            return

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

        if self.integration_paused:
            return

        # Record the open timestamp for this sensor
        if entity_id not in self._open_sensor_times:
            self._open_sensor_times[entity_id] = time.monotonic()

        # Cancel any close timer since something opened
        self._cancel_close_timer()

        # If already paused, nothing more to do
        if self.is_paused:
            return

        # If no open timer running, start one for this sensor
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

        if self.integration_paused:
            return

        # Remove this sensor from the open timestamps
        self._open_sensor_times.pop(entity_id, None)

        # If not paused, handle timer recalculation
        if not self.is_paused:
            if len(self._open_sensor_times) == 0:
                # All sensors closed - cancel the timer
                self._cancel_open_timer()
                _LOGGER.debug("Cancelled open timer - all sensors closed before timeout")
            elif self._pending_open_sensor == entity_id and self._open_timer is not None:
                # The triggering sensor closed but others are still open
                # Recalculate timer based on earliest still-open sensor
                self._recalculate_open_timer()
            return

        # If paused and all sensors are now closed, start close timer
        if self.is_paused and len(self._open_sensor_times) == 0:
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
        if self.integration_paused:
            self._cancel_open_timer()
            return

        # If already paused, nothing to do.
        if self.is_paused:
            return

        # Save the trigger sensor before cancelling (cancel clears _pending_open_sensor)
        trigger_sensor = self._pending_open_sensor

        # Cancel timer if still scheduled (e.g., when called manually in tests)
        self._cancel_open_timer()

        # Check if sensors are still open
        self._update_open_sensors()
        if len(self.open_sensors) == 0:
            _LOGGER.debug("Open timeout expired but all sensors are closed")
            return

        _LOGGER.info(
            "Open timeout expired with %d sensors open. Pausing thermostat.",
            len(self.open_sensors),
        )

        # Mark paused immediately to avoid races with other callbacks.
        self.is_paused = True

        # Store the trigger sensor for notifications
        self.trigger_sensor = trigger_sensor

        # Get current HVAC mode before turning off
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state:
            self.previous_hvac_mode = climate_state.state
        else:
            self.previous_hvac_mode = HVACMode.AUTO

        # If supported, set fan mode to auto (or off fallback) before turning HVAC off.
        if climate_state:
            supported = climate_state.attributes.get("supported_features", 0)
            fan_modes = climate_state.attributes.get("fan_modes")
            current_fan = climate_state.attributes.get("fan_mode")
            if (
                isinstance(fan_modes, list)
                and (supported & ClimateEntityFeature.FAN_MODE)
            ):
                desired_fan = None
                if "auto" in fan_modes:
                    desired_fan = "auto"
                elif "off" in fan_modes:
                    desired_fan = "off"

                if desired_fan and current_fan != desired_fan:
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN,
                        "set_fan_mode",
                        {
                            "entity_id": self.thermostat,
                            "fan_mode": desired_fan,
                        },
                        blocking=True,
                    )

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

        # Send notification
        await self._async_send_notification(paused=True)

        # Notify listeners
        self.async_set_updated_data(None)

        _LOGGER.info("Thermostat paused. Previous mode: %s", self.previous_hvac_mode)

    async def _async_close_timeout_expired(self) -> None:
        """Handle close timeout expiration - resume the thermostat."""
        # Cancel timer if still scheduled (e.g., when called manually in tests)
        self._cancel_close_timer()

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

        # Clear paused state before restoring HVAC mode so our own restore doesn't
        # get interpreted as a manual override.
        self.is_paused = False

        # Restore previous HVAC mode (unless respecting user's off choice)
        should_restore = True
        if self.previous_hvac_mode == HVACMode.OFF:
            if self.respect_user_off:
                _LOGGER.info(
                    "Thermostat was off before pause and respect_user_off is enabled. "
                    "Keeping thermostat off."
                )
                should_restore = False
            else:
                _LOGGER.info(
                    "Thermostat was off before pause but respect_user_off is disabled. "
                    "Will resume to last known active mode."
                )
                # Use the last known non-off mode if available
                if self._last_known_hvac_mode and self._last_known_hvac_mode != HVACMode.OFF:
                    self.previous_hvac_mode = self._last_known_hvac_mode

        if should_restore and self.previous_hvac_mode and self.previous_hvac_mode != HVACMode.OFF:
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

        self.trigger_sensor = None

        # Immediately evaluate thermostat state to handle satiation
        # This ensures we don't blindly turn the thermostat on if rooms are already
        # at target temperature, or correctly turn it on if rooms need conditioning
        await self.async_update_thermostat_state()

        # Update vents based on new state
        await self.async_update_vents()

        # Notify listeners
        self.async_set_updated_data(None)

        _LOGGER.info("Thermostat resumed to mode: %s", self.previous_hvac_mode)

    async def async_pause(self) -> None:
        """Pause the thermostat via service call (bypasses sensor checks)."""
        if self.is_paused:
            _LOGGER.info("Thermostat already paused")
            return

        if self.integration_paused:
            _LOGGER.info("Integration paused; ignoring pause request")
            return

        _LOGGER.info("Pausing thermostat via service call")

        # Mark paused immediately to avoid races with other callbacks.
        self.is_paused = True

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

        # Send notification
        await self._async_send_notification(paused=True)

        # Notify listeners
        self.async_set_updated_data(None)

        _LOGGER.info("Thermostat paused via service. Previous mode: %s", self.previous_hvac_mode)

    async def async_resume(self) -> None:
        """Resume the thermostat via service call (bypasses sensor checks)."""
        if not self.is_paused:
            _LOGGER.info("Thermostat not paused")
            return

        if self.integration_paused:
            _LOGGER.info("Integration paused; ignoring resume request")
            return

        _LOGGER.info("Resuming thermostat via service call")

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

        _LOGGER.info("Thermostat resumed via service to mode: %s", self.previous_hvac_mode)

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
