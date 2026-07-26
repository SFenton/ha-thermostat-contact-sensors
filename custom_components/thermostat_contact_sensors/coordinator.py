"""Coordinator for Thermostat Contact Sensors integration."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
import logging
import time
from typing import Any

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.components.climate import ClimateEntityFeature
from homeassistant.components.recorder import get_instance as get_recorder_instance
from homeassistant.components.recorder.history import get_significant_states
from homeassistant.const import (
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.recorder import DATA_INSTANCE as RECORDER_DATA_INSTANCE
from homeassistant.helpers.template import Template
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_ENABLED,
    CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
    CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED,
    CONF_AREA_VENT_OPEN_DELAY_SECONDS,
    CONF_AWAY_COOL_TEMP_DIFF,
    CONF_AWAY_HEAT_TEMP_DIFF,
    CONF_AWAY_PRESENCE_ENTITY,
    CONF_CLOSE_TIMEOUT,
    CONF_ECO_MODE_CRITICAL_TRACKING,
    CONF_PREDICTIVE_ACTIVITY_ENTITIES,
    CONF_PREDICTIVE_ACTIVITY_HEAT_GAIN,
    CONF_PREDICTIVE_ALLOW_AWAY,
    CONF_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE,
    CONF_PREDICTIVE_AUTO_ADJUST,
    CONF_PREDICTIVE_COMFORT_ENABLED,
    CONF_PREDICTIVE_COMFORT_HIGH,
    CONF_PREDICTIVE_COMFORT_LOW,
    CONF_PREDICTIVE_EVALUATION_INTERVAL,
    CONF_PREDICTIVE_HISTORY_LEARNING_ENABLED,
    CONF_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
    CONF_PREDICTIVE_HUMIDITY_SENSITIVITY,
    CONF_PREDICTIVE_HUMIDITY_SENSORS,
    CONF_PREDICTIVE_LEARNING_REFRESH_INTERVAL,
    CONF_PREDICTIVE_LEARNING_WINDOW_MINUTES,
    CONF_PREDICTIVE_LOOKAHEAD_HOURS,
    CONF_PREDICTIVE_MAX_LEARNED_HEAT_GAIN,
    CONF_PREDICTIVE_MEANINGFUL_TEMP_DELTA,
    CONF_PREDICTIVE_MIN_LEARNING_SAMPLES,
    CONF_PREDICTIVE_OUTDOOR_INFLUENCE,
    CONF_PREDICTIVE_PRECOOL_OFFSET,
    CONF_PREDICTIVE_PREHEAT_OFFSET,
    CONF_PREDICTIVE_RAIN_COOLING,
    CONF_PREDICTIVE_TEMPERATURE_SENSORS,
    CONF_PREDICTIVE_TRIGGER_MARGIN,
    CONF_PREDICTIVE_TREND_WEIGHT,
    CONF_PREDICTIVE_WEATHER_ENTITY,
    CONF_GRACE_PERIOD_MINUTES,
    CONF_COOLING_BOOST_OFFSET,
    CONF_HEATING_BOOST_OFFSET,
    CONF_MAX_CLOSED_VENTS,
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
    CONF_VACATION_MODE_ENTITY,
    DEFAULT_AWAY_COOL_TEMP_DIFF,
    DEFAULT_AWAY_HEAT_TEMP_DIFF,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_COOLING_BOOST_OFFSET,
    DEFAULT_ECO_MODE_CRITICAL_TRACKING,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_HEATING_BOOST_OFFSET,
    DEFAULT_MAX_CLOSED_VENTS,
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
    DEFAULT_PREDICTIVE_ACTIVITY_HEAT_GAIN,
    DEFAULT_PREDICTIVE_ALLOW_AWAY,
    DEFAULT_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE,
    DEFAULT_PREDICTIVE_AUTO_ADJUST,
    DEFAULT_PREDICTIVE_COMFORT_ENABLED,
    DEFAULT_PREDICTIVE_COMFORT_HIGH,
    DEFAULT_PREDICTIVE_COMFORT_LOW,
    DEFAULT_PREDICTIVE_EVALUATION_INTERVAL,
    DEFAULT_PREDICTIVE_HISTORY_LEARNING_ENABLED,
    DEFAULT_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
    DEFAULT_PREDICTIVE_HUMIDITY_SENSITIVITY,
    DEFAULT_PREDICTIVE_LEARNING_REFRESH_INTERVAL,
    DEFAULT_PREDICTIVE_LEARNING_WINDOW_MINUTES,
    DEFAULT_PREDICTIVE_LOOKAHEAD_HOURS,
    DEFAULT_PREDICTIVE_MAX_LEARNED_HEAT_GAIN,
    DEFAULT_PREDICTIVE_MEANINGFUL_TEMP_DELTA,
    DEFAULT_PREDICTIVE_MIN_LEARNING_SAMPLES,
    DEFAULT_PREDICTIVE_OUTDOOR_INFLUENCE,
    DEFAULT_PREDICTIVE_PRECOOL_OFFSET,
    DEFAULT_PREDICTIVE_PREHEAT_OFFSET,
    DEFAULT_PREDICTIVE_RAIN_COOLING,
    DEFAULT_PREDICTIVE_TRIGGER_MARGIN,
    DEFAULT_PREDICTIVE_TREND_WEIGHT,
    DEFAULT_TEMPERATURE_DEADBAND,
    DEFAULT_UNOCCUPIED_COOLING_THRESHOLD,
    DEFAULT_UNOCCUPIED_HEATING_THRESHOLD,
    DEFAULT_VENT_DEBOUNCE_SECONDS,
    DEFAULT_VENT_OPEN_DELAY_SECONDS,
    DEFAULT_VACATION_MODE_ENTITY,
    DOMAIN,
    ECO_CRITICAL_ALL,
    ECO_CRITICAL_NONE,
    ECO_CRITICAL_SELECT,
    PREDICTIVE_MODE_DISABLED,
    PREDICTIVE_MODE_IDLE,
    PREDICTIVE_MODE_INSUFFICIENT_DATA,
    PREDICTIVE_MODE_PRE_COOL,
    PREDICTIVE_MODE_PRE_HEAT,
)
from .occupancy import RoomOccupancyTracker
from .thermostat_control import (
    ThermostatAction,
    ThermostatController,
    ThermostatState,
    get_temperature_from_state,
    infer_temperature_unit_from_targets,
    is_room_satiated_for_cool,
    is_room_satiated_for_heat,
    is_room_satiated_for_heat_cool,
)
from .vent_control import VentController, VentControlState

_LOGGER = logging.getLogger(__name__)

WEATHER_DOMAIN = "weather"
SERVICE_GET_FORECASTS = "get_forecasts"
FORECAST_TYPE_HOURLY = "hourly"
HUMIDITY_COMFORT_BASELINE = 45.0
GLOBAL_WEATHER_ENTITY_PRIORITY = ("weather.pirate_weather", "weather.forecast_home")
ACTIVE_STATES = {
    "on",
    "home",
    "active",
    "playing",
    "heat",
    "cool",
    "heat_cool",
    "auto",
}
VACATION_ACTIVE_STATES = {
    STATE_ON,
    "true",
    "active",
    "vacation",
}
RAINY_CONDITIONS = {
    "hail",
    "lightning-rainy",
    "pouring",
    "rainy",
    "snowy",
    "snowy-rainy",
}


@dataclass(frozen=True)
class VentOnlyRoomTemperatureState:
    """Minimal temperature state used only for vent control.

    This intentionally does NOT participate in thermostat on/off decisions.
    """

    sensor_readings: dict[str, float]
    determining_temperature: float | None
    determining_sensor: str | None = None
    is_satiated: bool = False
    is_critical: bool = False
    critical_reason: str | None = None
    target_temperature: float | None = None


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
        self._control_lock = asyncio.Lock()
        self._restoring_hvac_mode = False
        temperature_unit = UnitOfTemperature.FAHRENHEIT
        thermostat_state = hass.states.get(thermostat)
        if thermostat_state is not None:
            state_unit = thermostat_state.attributes.get("unit_of_measurement")
            if state_unit in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
                temperature_unit = state_unit
        if temperature_unit not in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
            temperature_unit = UnitOfTemperature.FAHRENHEIT
        self._temperature_unit = temperature_unit

        # Track last known non-off HVAC mode for manual override detection
        self._last_known_hvac_mode: str | None = None

        # Predictive comfort tracking
        self.predictive_result: dict[str, Any] = self._predictive_disabled_result()
        self.predictive_learning_result: dict[str, Any] = (
            self._predictive_learning_disabled_result()
        )
        self._activity_heat_gains: dict[str, float] = {}
        self._last_predictive_learning_update: datetime | None = None

        # Listener cleanup
        self._unsub_state_change: callable | None = None
        self._unsub_thermostat_state_change: callable | None = None
        self._unsub_temp_sensor_state_change: callable | None = None
        self._unsub_presence_state_change: callable | None = None
        self._unsub_predictive_state_change: callable | None = None
        self._unsub_predictive_interval: callable | None = None

        # Away mode tracking
        self._presence_is_away: bool = False
        self._vacation_mode_is_active: bool = False
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
            max_closed_vents=self._options.get(
                CONF_MAX_CLOSED_VENTS, DEFAULT_MAX_CLOSED_VENTS
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

        # Cached inferred mode used specifically for vent priority when the thermostat
        # is OFF/unknown. This is recomputed whenever determining_temperature changes.
        self._last_vent_effective_mode: HVACMode | None = None
        self._last_vent_infer_targets: tuple[float | None, float | None] = (None, None)
        self._last_room_determining_temperatures: dict[str, float | None] = {}

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
    def temperature_unit(self) -> UnitOfTemperature | str:
        """Return the temperature unit used for internal comparisons."""
        return self._temperature_unit

    @property
    def options(self) -> dict[str, Any]:
        """Return a copy of the current coordinator options."""
        return dict(self._options)

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
        return getattr(self, "_is_away", False)

    @property
    def away_mode_configured(self) -> bool:
        """Return whether away mode has been configured with a presence entity."""
        return bool(self.away_presence_entity)

    @property
    def vacation_mode_entity(self) -> str:
        """Return the entity used to detect vacation mode."""
        configured = self._options.get(CONF_VACATION_MODE_ENTITY, "")
        return configured or DEFAULT_VACATION_MODE_ENTITY

    @property
    def vacation_mode_active(self) -> bool:
        """Return whether vacation mode is currently active."""
        return getattr(self, "_vacation_mode_is_active", False)

    @property
    def predictive_comfort_enabled(self) -> bool:
        """Return whether predictive comfort evaluation is enabled."""
        return bool(
            self._options.get(
                CONF_PREDICTIVE_COMFORT_ENABLED,
                DEFAULT_PREDICTIVE_COMFORT_ENABLED,
            )
        )

    @property
    def predictive_auto_adjust(self) -> bool:
        """Return whether predictive comfort may update thermostat setpoints."""
        return bool(
            self._options.get(
                CONF_PREDICTIVE_AUTO_ADJUST,
                DEFAULT_PREDICTIVE_AUTO_ADJUST,
            )
        )

    @property
    def predictive_allow_hvac_mode_change(self) -> bool:
        """Return whether predictive comfort may change HVAC modes."""
        return bool(
            self._options.get(
                CONF_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE,
                DEFAULT_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE,
            )
        )

    @property
    def predictive_allow_away(self) -> bool:
        """Return whether predictive comfort may adjust while away."""
        return bool(
            self._options.get(
                CONF_PREDICTIVE_ALLOW_AWAY,
                DEFAULT_PREDICTIVE_ALLOW_AWAY,
            )
        )

    @property
    def predictive_weather_entity(self) -> str:
        """Return the configured weather entity used for predictive comfort."""
        return self._options.get(CONF_PREDICTIVE_WEATHER_ENTITY, "")

    @property
    def predictive_history_learning_enabled(self) -> bool:
        """Return whether predictive comfort should learn from history."""
        return bool(
            self._options.get(
                CONF_PREDICTIVE_HISTORY_LEARNING_ENABLED,
                DEFAULT_PREDICTIVE_HISTORY_LEARNING_ENABLED,
            )
        )

    @property
    def predictive_temperature_sensors(self) -> list[str]:
        """Return indoor temperature sensors used for predictive comfort."""
        configured = self._entity_list_option(CONF_PREDICTIVE_TEMPERATURE_SENSORS)
        if configured:
            return configured

        return self._area_entity_list(CONF_TEMPERATURE_SENSORS)

    @property
    def predictive_humidity_sensors(self) -> list[str]:
        """Return humidity sensors used for predictive comfort."""
        return self._entity_list_option(CONF_PREDICTIVE_HUMIDITY_SENSORS)

    @property
    def predictive_activity_entities(self) -> list[str]:
        """Return activity entities used for predictive comfort."""
        entities = self._entity_list_option(CONF_PREDICTIVE_ACTIVITY_ENTITIES)
        entities.extend(self._area_entity_list(CONF_PREDICTIVE_ACTIVITY_ENTITIES))
        return list(dict.fromkeys(entities))

    @property
    def predictive_mode(self) -> str:
        """Return the current predictive comfort mode."""
        return self.predictive_result.get("mode", PREDICTIVE_MODE_DISABLED)

    def _predictive_hvac_mode_from_result(
        self,
        result: dict[str, Any],
    ) -> HVACMode | None:
        """Return the target HVAC mode from a predictive result."""
        target_hvac_mode = result.get("target_hvac_mode")
        if isinstance(target_hvac_mode, HVACMode):
            return target_hvac_mode
        if target_hvac_mode is None:
            return None

        try:
            return HVACMode(str(target_hvac_mode))
        except ValueError:
            return None

    def _predictive_control_request(
        self,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a Predictive Comfort demand for the main thermostat controller."""
        predictive_result = result or self.predictive_result

        if self.integration_paused:
            return {"eligible": False, "status": "skipped_integration_paused"}

        if not self.predictive_auto_adjust:
            return {"eligible": False, "status": "auto_adjust_disabled"}

        if self.vacation_mode_active:
            return {"eligible": False, "status": "skipped_vacation_mode"}

        if self.away_mode_configured and self.is_away and not self.predictive_allow_away:
            return {"eligible": False, "status": "skipped_away_mode"}

        if predictive_result.get("mode") not in (
            PREDICTIVE_MODE_PRE_COOL,
            PREDICTIVE_MODE_PRE_HEAT,
        ):
            return {"eligible": False, "status": "no_adjustment_needed"}

        # Gate on the debounced pause state only. Checking `open_count` directly
        # would revoke the predictive demand the instant a door is opened, which
        # can turn the thermostat off (and trip the min-cycle lockout) for an
        # open lasting only seconds. The room-demand path uses the same debounced
        # `is_paused` signal via `set_paused_by_contact_sensors`.
        if self.is_paused:
            return {"eligible": False, "status": "skipped_contact_sensor_open"}

        target_temperature = predictive_result.get("target_temperature")
        if target_temperature is None:
            return {"eligible": False, "status": "missing_target_temperature"}

        target_hvac_mode = self._predictive_hvac_mode_from_result(predictive_result)
        if target_hvac_mode not in (HVACMode.HEAT, HVACMode.COOL):
            return {"eligible": False, "status": "missing_target_hvac_mode"}

        climate_state = self.hass.states.get(self.thermostat)
        if climate_state is None or climate_state.state in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            return {"eligible": False, "status": "thermostat_unavailable"}

        supported_modes = climate_state.attributes.get("hvac_modes", [])
        if isinstance(supported_modes, list):
            supported_mode_values = {
                mode_value
                for mode in supported_modes
                if (mode_value := self._hvac_mode_value(mode)) is not None
            }
        else:
            supported_mode_values = set()
        if (
            supported_mode_values
            and target_hvac_mode.value not in supported_mode_values
        ):
            return {"eligible": False, "status": "target_hvac_mode_unavailable"}

        if (
            climate_state.state != target_hvac_mode.value
            and not self.predictive_allow_hvac_mode_change
        ):
            return {"eligible": False, "status": "hvac_mode_changes_disabled"}

        try:
            target_temperature_float = float(target_temperature)
        except (TypeError, ValueError):
            return {"eligible": False, "status": "missing_target_temperature"}

        return {
            "eligible": True,
            "status": "coordinating",
            "hvac_mode": target_hvac_mode,
            "target_temperature": target_temperature_float,
            "reason": predictive_result.get("reason"),
        }

    def _entity_list_option(self, key: str) -> list[str]:
        """Return an option value normalized to a list of entity IDs."""
        value = self._options.get(key, [])
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [entity_id for entity_id in value if isinstance(entity_id, str)]
        return []

    def _area_entity_list(self, key: str) -> list[str]:
        """Return area-level entity IDs for a config key."""
        entities: list[str] = []
        for area_config in self._areas_config.values():
            if not area_config.get(CONF_AREA_ENABLED, True):
                continue
            value = area_config.get(key, [])
            if isinstance(value, str):
                if value:
                    entities.append(value)
            elif isinstance(value, list):
                entities.extend(
                    entity_id for entity_id in value if isinstance(entity_id, str)
                )
        return list(dict.fromkeys(entities))

    def _option_float(self, key: str, default: float) -> float:
        """Return a numeric option as a float."""
        value = self._options.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Invalid numeric option %s=%r; using %s", key, value, default)
            return float(default)

    def _option_int(self, key: str, default: int) -> int:
        """Return a numeric option as an int."""
        return int(round(self._option_float(key, float(default))))

    def _predictive_disabled_result(self) -> dict[str, Any]:
        """Return a disabled predictive comfort result."""
        return {
            "mode": PREDICTIVE_MODE_DISABLED,
            "reason": "Predictive Comfort Mode is disabled",
            "reasons": ["Predictive Comfort Mode is disabled"],
            "auto_adjust_enabled": False,
            "allow_hvac_mode_change": self.predictive_allow_hvac_mode_change,
            "allow_away": self.predictive_allow_away,
            "away_mode_active": self.is_away,
        }

    def _predictive_learning_disabled_result(self) -> dict[str, Any]:
        """Return a disabled predictive learning result."""
        return {
            "status": "disabled",
            "reason": "Predictive history learning is disabled",
        }

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

    @staticmethod
    def _presence_state_value(state: State | None) -> bool | None:
        """Convert a presence state to Away, Home, or no valid update."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        state_value = state.state.lower()
        return state_value in (STATE_NOT_HOME, STATE_OFF, "false", "away")

    @staticmethod
    def _vacation_state_value(state: State | None) -> bool | None:
        """Convert a Vacation state to active, inactive, or no valid update."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        return state.state.lower() in VACATION_ACTIVE_STATES

    def _combined_away_state(self) -> bool:
        """Combine the last valid Vacation and presence states."""
        return self.away_mode_configured and (
            self._vacation_mode_is_active or self._presence_is_away
        )

    @callback
    def _async_away_state_changed(self, event) -> None:
        """Handle presence or Vacation Mode state changes."""
        entity_id: str | None = event.data.get("entity_id")
        new_state: State | None = event.data.get("new_state")
        if not entity_id or new_state is None:
            return

        updated = False
        vacation_updated = False
        if entity_id == self.away_presence_entity:
            presence_is_away = self._presence_state_value(new_state)
            if presence_is_away is not None:
                self._presence_is_away = presence_is_away
                updated = True
        if entity_id == self.vacation_mode_entity:
            vacation_mode_is_active = self._vacation_state_value(new_state)
            if vacation_mode_is_active is not None:
                self._vacation_mode_is_active = vacation_mode_is_active
                updated = True
                vacation_updated = True
        if not updated:
            return

        was_away = self._is_away
        self._is_away = self._combined_away_state()

        if was_away != self._is_away:
            _LOGGER.info("Away mode changed: is_away=%s", self._is_away)
            self.hass.async_create_task(self._async_occupancy_changed())
        if vacation_updated and self.predictive_comfort_enabled:
            self.hass.async_create_task(self.async_evaluate_predictive_comfort())

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

    def _hvac_mode_value(self, hvac_mode: HVACMode | str | None) -> str | None:
        """Return a usable HVAC mode string, ignoring unavailable states."""
        if hvac_mode is None:
            return None

        mode_value = hvac_mode.value if hasattr(hvac_mode, "value") else str(hvac_mode)
        if mode_value in (STATE_UNAVAILABLE, STATE_UNKNOWN, ""):
            return None

        return mode_value

    def _supported_hvac_mode_values(self) -> set[str] | None:
        """Return supported HVAC modes for the physical thermostat if available."""
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state is None:
            return None

        hvac_modes = climate_state.attributes.get("hvac_modes")
        if not isinstance(hvac_modes, list):
            return None

        supported_modes: set[str] = set()
        for hvac_mode in hvac_modes:
            if mode_value := self._hvac_mode_value(hvac_mode):
                supported_modes.add(mode_value)

        return supported_modes

    def _valid_hvac_mode(
        self,
        hvac_mode: HVACMode | str | None,
        *,
        allow_off: bool,
    ) -> str | None:
        """Return a restorable HVAC mode if the thermostat can accept it."""
        mode_value = self._hvac_mode_value(hvac_mode)
        if mode_value is None:
            return None

        if mode_value == HVACMode.OFF.value and not allow_off:
            return None

        supported_modes = self._supported_hvac_mode_values()
        if supported_modes is not None and mode_value not in supported_modes:
            _LOGGER.debug(
                "Ignoring HVAC mode %s because %s only supports %s",
                mode_value,
                self.thermostat,
                sorted(supported_modes),
            )
            return None

        return mode_value

    def _capture_previous_hvac_mode(self) -> str | None:
        """Capture the current or last known HVAC mode before pausing."""
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state:
            current_mode = self._valid_hvac_mode(climate_state.state, allow_off=True)
            if current_mode is not None:
                return current_mode

        return self._valid_hvac_mode(self._last_known_hvac_mode, allow_off=False)

    def _resume_hvac_mode(self) -> str | None:
        """Resolve the HVAC mode to restore when contact sensors are closed."""
        previous_mode = self._valid_hvac_mode(self.previous_hvac_mode, allow_off=True)
        if previous_mode == HVACMode.OFF.value:
            if self.respect_user_off:
                _LOGGER.info(
                    "Thermostat was off before pause and respect_user_off is enabled. "
                    "Keeping thermostat off."
                )
                return None

            _LOGGER.info(
                "Thermostat was off before pause but respect_user_off is disabled. "
                "Will resume to last known active mode."
            )
            previous_mode = None

        if previous_mode is not None:
            return previous_mode

        fallback_mode = self._valid_hvac_mode(self._last_known_hvac_mode, allow_off=False)
        if fallback_mode is not None:
            _LOGGER.info(
                "Previous HVAC mode %s is not restorable. Resuming to last known mode %s.",
                self.previous_hvac_mode,
                fallback_mode,
            )
            return fallback_mode

        if self.previous_hvac_mode:
            _LOGGER.info(
                "Previous HVAC mode %s is not restorable and no last known mode exists.",
                self.previous_hvac_mode,
            )
        return None

    def _set_previous_hvac_mode_after_resume(self, resume_hvac_mode: str | None) -> None:
        """Update stored previous mode after a successful resume."""
        if resume_hvac_mode is not None:
            self.previous_hvac_mode = resume_hvac_mode
            return

        if (
            self.previous_hvac_mode
            and self._valid_hvac_mode(self.previous_hvac_mode, allow_off=True) is None
        ):
            self.previous_hvac_mode = None

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

    def _build_vent_only_room_temp_states(
        self, state: ThermostatState | None = None
    ) -> dict[str, VentOnlyRoomTemperatureState]:
        """Build temperature states for vent control from configured sensors.

        This is intentionally independent of TSR and thermostat decision-making.
        It reads the configured per-area temperature sensors directly from HA
        so *inactive/untracked* rooms can still influence vent prioritization.
        Track-only-when-occupied rooms are still included here so the vent
        safety budget can choose the safest ignored rooms to keep closed.
        """
        state_for_mode = state or self._last_thermostat_state
        mode_for_eval = None
        if state_for_mode is not None:
            if state_for_mode.hvac_mode in (HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL):
                mode_for_eval = state_for_mode.hvac_mode
            elif state_for_mode.inferred_hvac_mode in (
                HVACMode.HEAT,
                HVACMode.COOL,
                HVACMode.HEAT_COOL,
            ):
                mode_for_eval = state_for_mode.inferred_hvac_mode

        deadband = self._options.get(CONF_TEMPERATURE_DEADBAND, DEFAULT_TEMPERATURE_DEADBAND)
        target_unit = self.temperature_unit
        if state_for_mode is not None:
            target_unit = infer_temperature_unit_from_targets(
                state_for_mode.target_temperature,
                state_for_mode.target_temp_low,
                state_for_mode.target_temp_high,
                fallback=self.temperature_unit,
            )

        result: dict[str, VentOnlyRoomTemperatureState] = {}
        for area_id, sensors in self.get_area_temp_sensors().items():
            area_target_temp = None
            area_target_temp_low = None
            area_target_temp_high = None
            area_target_unit = target_unit
            if state_for_mode is not None:
                area_target_temp = state_for_mode.target_temperature
                area_target_temp_low = state_for_mode.target_temp_low
                area_target_temp_high = state_for_mode.target_temp_high
                if area_target_temp is None:
                    if mode_for_eval == HVACMode.HEAT:
                        area_target_temp = area_target_temp_low
                    elif mode_for_eval == HVACMode.COOL:
                        area_target_temp = area_target_temp_high
                    elif (
                        mode_for_eval == HVACMode.HEAT_COOL
                        and area_target_temp_low is not None
                        and area_target_temp_high is not None
                    ):
                        area_target_temp = (area_target_temp_low + area_target_temp_high) / 2

                area_thermostats = getattr(self, "area_thermostats", {})
                if area_id in area_thermostats:
                    (
                        area_target_temp,
                        area_target_temp_low,
                        area_target_temp_high,
                    ) = self.thermostat_controller.get_area_target_temperatures(
                        area_id,
                        hvac_mode_override=mode_for_eval,
                    )
                area_target_unit = infer_temperature_unit_from_targets(
                    area_target_temp,
                    area_target_temp_low,
                    area_target_temp_high,
                    fallback=self.temperature_unit,
                )

            readings: dict[str, float] = {}
            for entity_id in sensors:
                state = self.hass.states.get(entity_id)
                temp = get_temperature_from_state(state, area_target_unit)
                if temp is None:
                    continue
                readings[entity_id] = temp

            if not readings:
                result[area_id] = VentOnlyRoomTemperatureState(
                    determining_temperature=None,
                    determining_sensor=None,
                    sensor_readings={},
                )
                continue

            determining_sensor: str | None
            determining_temp: float | None
            is_satiated: bool

            if state_for_mode is not None and mode_for_eval == HVACMode.HEAT:
                target = area_target_temp
                if target is None:
                    target = area_target_temp_low
                if target is not None:
                    is_satiated, determining_sensor, determining_temp = is_room_satiated_for_heat(
                        readings, target, deadband
                    )
                else:
                    is_satiated = False
                    avg = sum(readings.values()) / len(readings)
                    determining_sensor, determining_temp = min(
                        readings.items(), key=lambda x: abs(x[1] - avg)
                    )
            elif state_for_mode is not None and mode_for_eval == HVACMode.COOL:
                target = area_target_temp
                if target is None:
                    target = area_target_temp_high
                if target is not None:
                    is_satiated, determining_sensor, determining_temp = is_room_satiated_for_cool(
                        readings, target, deadband
                    )
                else:
                    is_satiated = False
                    avg = sum(readings.values()) / len(readings)
                    determining_sensor, determining_temp = min(
                        readings.items(), key=lambda x: abs(x[1] - avg)
                    )
            elif state_for_mode is not None and mode_for_eval == HVACMode.HEAT_COOL:
                if (
                    area_target_temp_low is not None
                    and area_target_temp_high is not None
                ):
                    (
                        is_satiated,
                        determining_sensor,
                        determining_temp,
                    ) = is_room_satiated_for_heat_cool(
                        readings,
                        area_target_temp_low,
                        area_target_temp_high,
                        deadband,
                    )
                else:
                    is_satiated = False
                    avg = sum(readings.values()) / len(readings)
                    determining_sensor, determining_temp = min(
                        readings.items(), key=lambda x: abs(x[1] - avg)
                    )
            else:
                # No reliable mode/targets. Match thermostat-control fallback:
                # use average, and pick the sensor closest to that average.
                is_satiated = False
                avg = sum(readings.values()) / len(readings)
                determining_sensor, determining_temp = min(
                    readings.items(), key=lambda x: abs(x[1] - avg)
                )

            is_critical = False
            critical_reason = None
            target_temperature = area_target_temp
            if mode_for_eval == HVACMode.HEAT:
                target = area_target_temp if area_target_temp is not None else area_target_temp_low
                target_temperature = target
                if target is not None:
                    _, hottest_temp = max(readings.items(), key=lambda x: x[1])
                    threshold = self.thermostat_controller.unoccupied_heating_threshold
                    if hottest_temp < target - threshold:
                        is_critical = True
                        critical_reason = (
                            f"Temperature {hottest_temp:.1f}° is {target - hottest_temp:.1f}° "
                            f"below heat target {target:.1f}° (threshold: {threshold:.1f}°)"
                        )
            elif mode_for_eval == HVACMode.COOL:
                target = area_target_temp if area_target_temp is not None else area_target_temp_high
                target_temperature = target
                if target is not None:
                    _, coldest_temp = min(readings.items(), key=lambda x: x[1])
                    threshold = self.thermostat_controller.unoccupied_cooling_threshold
                    if coldest_temp > target + threshold:
                        is_critical = True
                        critical_reason = (
                            f"Temperature {coldest_temp:.1f}° is {coldest_temp - target:.1f}° "
                            f"above cool target {target:.1f}° (threshold: {threshold:.1f}°)"
                        )
            elif (
                mode_for_eval == HVACMode.HEAT_COOL
                and area_target_temp_low is not None
                and area_target_temp_high is not None
            ):
                target_temperature = (area_target_temp_low + area_target_temp_high) / 2
                _, coldest_temp = min(readings.items(), key=lambda x: x[1])
                _, warmest_temp = max(readings.items(), key=lambda x: x[1])
                heat_threshold = self.thermostat_controller.unoccupied_heating_threshold
                cool_threshold = self.thermostat_controller.unoccupied_cooling_threshold
                if coldest_temp < area_target_temp_low - heat_threshold:
                    is_critical = True
                    critical_reason = (
                        f"Temperature {coldest_temp:.1f}° is {area_target_temp_low - coldest_temp:.1f}° "
                        f"below heat target {area_target_temp_low:.1f}° (threshold: {heat_threshold:.1f}°)"
                    )
                elif warmest_temp > area_target_temp_high + cool_threshold:
                    is_critical = True
                    critical_reason = (
                        f"Temperature {warmest_temp:.1f}° is {warmest_temp - area_target_temp_high:.1f}° "
                        f"above cool target {area_target_temp_high:.1f}° (threshold: {cool_threshold:.1f}°)"
                    )

            result[area_id] = VentOnlyRoomTemperatureState(
                determining_temperature=determining_temp,
                determining_sensor=determining_sensor,
                sensor_readings=readings,
                is_satiated=is_satiated,
                is_critical=is_critical,
                critical_reason=critical_reason,
                target_temperature=target_temperature,
            )

        return result

    def _get_room_temp_states_for_vent_control(self) -> dict[str, Any]:
        """Get the merged room temperature states used for vent control.

        This includes thermostat room states AND vent-only temperature states.
        Thermostat decision-making should only consider thermostat room states.
        """
        room_temp_states: dict[str, Any] = {}
        if self._last_thermostat_state:
            room_temp_states.update(self._last_thermostat_state.room_states)

        # Only add vent-only states for areas not already represented by the
        # thermostat controller.
        for area_id, vent_state in self._build_vent_only_room_temp_states().items():
            if area_id not in room_temp_states:
                room_temp_states[area_id] = vent_state

        return room_temp_states

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

    def _area_tracks_only_when_occupied(self, area_id: str) -> bool:
        """Return True if an area should be ignored while unoccupied."""
        area_config = self._areas_config.get(area_id, {})
        return bool(area_config.get(CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED, False))

    def _area_is_currently_occupied(self, area_id: str) -> bool:
        """Return True if the occupancy tracker currently sees the area occupied."""
        area = self.occupancy_tracker.areas.get(area_id)
        return bool(area and area.is_occupied)

    def _area_available_for_tracking(self, area_id: str) -> bool:
        """Return True if an area may participate in thermostat/vent decisions."""
        return (
            not self._area_tracks_only_when_occupied(area_id)
            or self._area_is_currently_occupied(area_id)
        )

    def _filter_trackable_areas(
        self, areas: list[Any]
    ) -> list[Any]:
        """Remove areas configured to track only when occupied if currently empty."""
        return [
            area
            for area in areas
            if self._area_available_for_tracking(area.area_id)
        ]

    def _unoccupied_track_only_area_ids(self) -> set[str]:
        """Return enabled areas that should stay closed/ignored until occupied."""
        return {
            area_id
            for area_id, area_config in self._areas_config.items()
            if area_config.get(CONF_AREA_ENABLED, True)
            and self._area_tracks_only_when_occupied(area_id)
            and not self._area_is_currently_occupied(area_id)
        }

    def update_thermostat_state(self) -> ThermostatState | None:
        """Evaluate and update the current thermostat control state.

        Returns:
            The updated ThermostatState.
        """
        # Get active and inactive areas from occupancy tracker
        all_active_areas = self._filter_trackable_areas(
            self.occupancy_tracker.active_areas
        )
        all_inactive_areas = self._filter_trackable_areas(
            self.occupancy_tracker.inactive_areas
        )
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
        predictive_request = self._predictive_control_request()
        self._last_thermostat_state = self.thermostat_controller.evaluate_thermostat_action(
            active_areas=active_areas,
            area_temp_sensors=area_temp_sensors,
            inactive_areas=inactive_areas,
            respect_user_off=self.respect_user_off,
            eco_mode=eco_mode_for_thermostat,
            eco_away_targets=eco_away_targets,
            # Trend/inferred HVAC mode should be based on all currently trackable
            # room sensors, independent of Eco/TSR/force-critical filtering.
            all_areas_for_trend=self._filter_trackable_areas(
                list(self.occupancy_tracker.areas.values())
            ),
            tracked_area_ids=tracked_area_ids,
            force_critical_area_ids=force_critical_area_ids,
            predictive_hvac_mode=predictive_request.get("hvac_mode"),
            predictive_target_temperature=predictive_request.get("target_temperature"),
            predictive_reason=predictive_request.get("reason"),
        )

        self._refresh_vent_effective_mode_if_needed(self._last_thermostat_state)

        return self._last_thermostat_state

    def _refresh_vent_effective_mode_if_needed(self, state: ThermostatState | None) -> None:
        """Recompute vent effective mode when determining_temperature changes.

        Vent control uses an inferred HEAT/COOL mode when the thermostat is OFF/unknown
        so it can prioritize rooms in the correct direction. We want that inference
        to update whenever any room's determining_temperature changes.
        """
        if state is None:
            self._last_vent_effective_mode = None
            self._last_vent_infer_targets = (None, None)
            self._last_room_determining_temperatures = {}
            return

        # Use a merged room-state map for vent-control inference.
        merged_room_states: dict[str, Any] = dict(state.room_states)
        for area_id, vent_state in self._build_vent_only_room_temp_states(state).items():
            if area_id not in merged_room_states:
                merged_room_states[area_id] = vent_state

        new_targets = (state.target_temp_low, state.target_temp_high)
        new_determining: dict[str, float | None] = {
            area_id: getattr(room_state, "determining_temperature", None)
            for area_id, room_state in merged_room_states.items()
        }

        if (
            new_targets == self._last_vent_infer_targets
            and new_determining == self._last_room_determining_temperatures
        ):
            return

        self._last_vent_infer_targets = new_targets
        self._last_room_determining_temperatures = new_determining
        self._last_vent_effective_mode = VentController.infer_effective_hvac_mode(
            merged_room_states,
            state.target_temp_low,
            state.target_temp_high,
        )

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

    async def async_update_thermostat_and_vents(self) -> ThermostatState | None:
        """Re-evaluate thermostat state and then vents.

        This is used by UI entities (switch/select) where a config toggle should
        take effect immediately for both thermostat and vent control.
        """
        if self.integration_paused:
            return self._last_thermostat_state

        async with self._control_lock:
            state = await self.async_update_thermostat_state()
            await self.async_update_vents()
            self.async_set_updated_data(None)
            return state

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
        self.vent_controller.max_closed_vents = options.get(
            CONF_MAX_CLOSED_VENTS, DEFAULT_MAX_CLOSED_VENTS
        )
        self.vent_controller.vent_open_delay_seconds = options.get(
            CONF_VENT_OPEN_DELAY_SECONDS, DEFAULT_VENT_OPEN_DELAY_SECONDS
        )
        self.vent_controller.vent_debounce_seconds = options.get(
            CONF_VENT_DEBOUNCE_SECONDS, DEFAULT_VENT_DEBOUNCE_SECONDS
        )
        self._setup_predictive_tracking()
        self.hass.async_create_task(
            self.async_evaluate_predictive_comfort(force_learning=True)
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

        # Vacation Mode overrides presence while active. Presence resumes control
        # immediately after Vacation Mode turns off.
        self._presence_is_away = False
        self._vacation_mode_is_active = False
        presence_is_away = self._presence_state_value(
            self.hass.states.get(self.away_presence_entity)
        )
        if presence_is_away is not None:
            self._presence_is_away = presence_is_away
        vacation_mode_is_active = self._vacation_state_value(
            self.hass.states.get(self.vacation_mode_entity)
        )
        if vacation_mode_is_active is not None:
            self._vacation_mode_is_active = vacation_mode_is_active
        self._is_away = self._combined_away_state()
        away_state_entities = {
            self.away_presence_entity,
            self.vacation_mode_entity,
        }
        away_state_entities.discard("")
        if away_state_entities:
            self._unsub_presence_state_change = async_track_state_change_event(
                self.hass,
                sorted(away_state_entities),
                self._async_away_state_changed,
            )

        # Initialize last known HVAC mode from current thermostat state
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state:
            self._last_known_hvac_mode = self._valid_hvac_mode(
                climate_state.state,
                allow_off=False,
            )

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

        self._setup_predictive_tracking()
        await self.async_evaluate_predictive_comfort()

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
        await self.async_update_thermostat_and_vents()

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

        self._teardown_predictive_tracking()

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
        await self.async_update_thermostat_and_vents()

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
        await self.async_update_thermostat_and_vents()

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
        active_areas = self._filter_trackable_areas(self.occupancy_tracker.active_areas)
        occupied_areas = self._filter_trackable_areas(
            self.occupancy_tracker.occupied_areas
        )
        excluded_area_ids = self._unoccupied_track_only_area_ids()

        # Get room temperature states and target temperatures from last thermostat state.
        # Room temperature states for vent control are merged with vent-only sensors.
        room_temp_states: dict[str, Any] = {}
        hvac_mode = None
        target_temp_low = None
        target_temp_high = None
        if self._last_thermostat_state:
            room_temp_states = self._get_room_temp_states_for_vent_control()
            hvac_mode = self._last_thermostat_state.hvac_mode
            target_temp_low = self._last_thermostat_state.target_temp_low
            target_temp_high = self._last_thermostat_state.target_temp_high
        else:
            room_temp_states = self._get_room_temp_states_for_vent_control()

        # When the thermostat is OFF/unknown, use the cached inferred mode for vent priority.
        # This is recomputed whenever determining_temperature changes in any room.
        if hvac_mode in (None, HVACMode.OFF) and self._last_vent_effective_mode is not None:
            hvac_mode = self._last_vent_effective_mode

        # Get per-area vent delay overrides
        area_vent_delays = self.get_area_vent_delays()

        # Only let Predictive Comfort relax vent gating when the mode it asked for
        # is the mode actually being delivered. A predictive demand that was vetoed
        # by conflicting room demand must not widen force-open.
        predictive_hvac_mode = None
        if (
            self._last_thermostat_state is not None
            and self._last_thermostat_state.predictive_hvac_mode is not None
            and self._last_thermostat_state.predictive_hvac_mode == hvac_mode
        ):
            predictive_hvac_mode = self._last_thermostat_state.predictive_hvac_mode

        tracked_area_ids = self.tracked_rooms if self.only_track_selected_rooms else set()
        force_track_when_critical_area_ids = {
            area_id
            for area_id in self._areas_config.keys()
            if self._area_has_critical_override(area_id)
        }

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
            eco_mode=self.eco_mode,
            only_track_selected_rooms=self.only_track_selected_rooms,
            tracked_area_ids=tracked_area_ids,
            force_track_when_critical_area_ids=force_track_when_critical_area_ids,
            excluded_area_ids=excluded_area_ids,
            predictive_hvac_mode=predictive_hvac_mode,
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

    def _setup_predictive_tracking(self) -> None:
        """Set up listeners for predictive comfort inputs."""
        self._teardown_predictive_tracking()

        if not self.predictive_comfort_enabled:
            self.predictive_result = self._predictive_disabled_result()
            self.async_set_updated_data(None)
            return

        tracked_entities = set(self.predictive_temperature_sensors)
        tracked_entities.update(self.predictive_humidity_sensors)
        tracked_entities.update(self.predictive_activity_entities)
        tracked_entities.discard(self.vacation_mode_entity)
        if weather_entity := self._resolved_predictive_weather_entity():
            tracked_entities.add(weather_entity)

        tracked_entities.discard("")
        if tracked_entities:
            self._unsub_predictive_state_change = async_track_state_change_event(
                self.hass,
                list(tracked_entities),
                self._async_predictive_state_changed,
            )

        interval = max(
            1,
            self._option_int(
                CONF_PREDICTIVE_EVALUATION_INTERVAL,
                DEFAULT_PREDICTIVE_EVALUATION_INTERVAL,
            ),
        )
        self._unsub_predictive_interval = async_track_time_interval(
            self.hass,
            self._async_predictive_interval,
            timedelta(minutes=interval),
        )

    def _teardown_predictive_tracking(self) -> None:
        """Cancel predictive comfort listeners."""
        if self._unsub_predictive_state_change:
            self._unsub_predictive_state_change()
            self._unsub_predictive_state_change = None

        if self._unsub_predictive_interval:
            self._unsub_predictive_interval()
            self._unsub_predictive_interval = None

    def _resolved_predictive_weather_entity(self) -> str:
        """Return the configured, shared, or auto-detected weather entity."""
        if configured_weather_entity := self.predictive_weather_entity:
            return configured_weather_entity

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == self.config_entry_id:
                continue
            shared_weather_entity = entry.options.get(CONF_PREDICTIVE_WEATHER_ENTITY)
            if shared_weather_entity and self.hass.states.get(shared_weather_entity):
                return shared_weather_entity

        for entity_id in GLOBAL_WEATHER_ENTITY_PRIORITY:
            if self.hass.states.get(entity_id):
                return entity_id

        for state in self.hass.states.async_all():
            if state.entity_id.startswith(f"{WEATHER_DOMAIN}."):
                return state.entity_id

        return ""

    @callback
    def _async_predictive_state_changed(self, event) -> None:
        """Handle predictive input state changes."""
        self.hass.async_create_task(self.async_evaluate_predictive_comfort())

    @callback
    def _async_predictive_interval(self, now: datetime) -> None:
        """Handle periodic predictive comfort evaluation."""
        self.hass.async_create_task(self.async_evaluate_predictive_comfort())

    async def async_evaluate_predictive_comfort(
        self,
        *,
        force_learning: bool = False,
    ) -> None:
        """Evaluate Predictive Comfort Mode and optionally adjust thermostat."""
        if not self.predictive_comfort_enabled:
            self.predictive_result = self._predictive_disabled_result()
            self.async_set_updated_data(None)
            return

        await self._async_update_predictive_learning(force=force_learning)
        result = await self._async_build_predictive_result()
        self.predictive_result = result
        await self._async_apply_predictive_result(result)
        self.async_set_updated_data(None)

    async def _async_update_predictive_learning(self, *, force: bool = False) -> None:
        """Update learned activity heat gains from room history."""
        if not self.predictive_history_learning_enabled:
            self._activity_heat_gains = {}
            self.predictive_learning_result = self._predictive_learning_disabled_result()
            return

        temperature_sensors = self.predictive_temperature_sensors
        activity_entities = self.predictive_activity_entities
        if not temperature_sensors or not activity_entities:
            self._activity_heat_gains = {}
            self.predictive_learning_result = {
                "status": "insufficient_config",
                "reason": (
                    "Select room temperature sensors and activity entities to learn "
                    "room heat-load impact"
                ),
            }
            return

        now = dt_util.utcnow()
        refresh_interval = self._option_int(
            CONF_PREDICTIVE_LEARNING_REFRESH_INTERVAL,
            DEFAULT_PREDICTIVE_LEARNING_REFRESH_INTERVAL,
        )
        if (
            not force
            and self._last_predictive_learning_update is not None
            and now - self._last_predictive_learning_update
            < timedelta(minutes=refresh_interval)
        ):
            return

        start_time = now - timedelta(
            days=self._option_int(
                CONF_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
                DEFAULT_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
            )
        )
        entity_ids = [*temperature_sensors, *activity_entities]

        try:
            history = await self._async_fetch_predictive_history(
                start_time,
                now,
                entity_ids,
            )
        except (HomeAssistantError, KeyError) as ex:
            self._activity_heat_gains = {}
            self.predictive_learning_result = {
                "status": "unavailable",
                "reason": f"Recorder history is unavailable: {ex}",
            }
            return

        self._activity_heat_gains = self._learn_activity_heat_gains(history)
        self._last_predictive_learning_update = now
        meaningful_entities = [
            entity_id
            for entity_id, heat_gain in self._activity_heat_gains.items()
            if heat_gain > 0
        ]
        self.predictive_learning_result = {
            "status": "ready",
            "reason": (
                f"Learned heat-load impact for {len(meaningful_entities)} "
                f"of {len(activity_entities)} configured activity entities"
            ),
            "lookback_days": self._option_int(
                CONF_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
                DEFAULT_PREDICTIVE_HISTORY_LOOKBACK_DAYS,
            ),
            "window_minutes": self._option_int(
                CONF_PREDICTIVE_LEARNING_WINDOW_MINUTES,
                DEFAULT_PREDICTIVE_LEARNING_WINDOW_MINUTES,
            ),
            "last_updated": now.isoformat(),
            "learned_heat_gains": self._activity_heat_gains,
        }

    async def _async_fetch_predictive_history(
        self,
        start_time: datetime,
        end_time: datetime,
        entity_ids: list[str],
    ) -> dict[str, list[State | dict[str, Any]]]:
        """Fetch recorder history for predictive learning."""
        if RECORDER_DATA_INSTANCE not in self.hass.data:
            raise HomeAssistantError("recorder integration is not loaded")

        recorder_instance = get_recorder_instance(self.hass)
        return await recorder_instance.async_add_executor_job(
            partial(
                get_significant_states,
                self.hass,
                start_time,
                end_time,
                entity_ids,
                None,
                True,
                False,
                False,
                True,
                False,
            )
        )

    def _learn_activity_heat_gains(
        self,
        history: dict[str, list[State | dict[str, Any]]],
    ) -> dict[str, float]:
        """Learn per-activity-entity heat gains from room temperature history."""
        temperature_history = self._temperature_history_by_entity(history)
        heat_gains: dict[str, float] = {}
        min_samples = self._option_int(
            CONF_PREDICTIVE_MIN_LEARNING_SAMPLES,
            DEFAULT_PREDICTIVE_MIN_LEARNING_SAMPLES,
        )
        meaningful_delta = self._option_float(
            CONF_PREDICTIVE_MEANINGFUL_TEMP_DELTA,
            DEFAULT_PREDICTIVE_MEANINGFUL_TEMP_DELTA,
        )
        max_gain = self._option_float(
            CONF_PREDICTIVE_MAX_LEARNED_HEAT_GAIN,
            DEFAULT_PREDICTIVE_MAX_LEARNED_HEAT_GAIN,
        )
        window = timedelta(
            minutes=self._option_int(
                CONF_PREDICTIVE_LEARNING_WINDOW_MINUTES,
                DEFAULT_PREDICTIVE_LEARNING_WINDOW_MINUTES,
            )
        )

        for entity_id in self.predictive_activity_entities:
            activation_times = self._activity_activation_times(history.get(entity_id, []))
            deltas = []
            for activation_time in activation_times:
                start_temperature = self._average_temperature_at(
                    temperature_history,
                    activation_time,
                )
                end_temperature = self._average_temperature_at(
                    temperature_history,
                    activation_time + window,
                )
                if start_temperature is None or end_temperature is None:
                    continue
                deltas.append(end_temperature - start_temperature)

            average_delta = sum(deltas) / len(deltas) if deltas else 0.0
            if len(deltas) >= min_samples and average_delta >= meaningful_delta:
                heat_gains[entity_id] = round(min(average_delta, max_gain), 2)
            else:
                heat_gains[entity_id] = 0.0

        return heat_gains

    def _temperature_history_by_entity(
        self,
        history: dict[str, list[State | dict[str, Any]]],
    ) -> dict[str, list[tuple[datetime, float]]]:
        """Return numeric temperature history by sensor."""
        temperature_history: dict[str, list[tuple[datetime, float]]] = {}
        for entity_id in self.predictive_temperature_sensors:
            readings = []
            for item in history.get(entity_id, []):
                timestamp = self._history_timestamp(item)
                value = self._float_or_none(self._history_state(item))
                if timestamp is not None and value is not None:
                    readings.append((timestamp, value))
            temperature_history[entity_id] = sorted(readings, key=lambda item: item[0])
        return temperature_history

    def _activity_activation_times(
        self,
        states: list[State | dict[str, Any]],
    ) -> list[datetime]:
        """Return times when an activity entity became active."""
        activation_times = []
        previous_active = False
        for item in sorted(
            states,
            key=lambda state_item: self._history_timestamp(state_item)
            or dt_util.utcnow(),
        ):
            timestamp = self._history_timestamp(item)
            if timestamp is None:
                continue
            active = self._is_activity_state_value(self._history_state(item))
            if active and not previous_active:
                activation_times.append(timestamp)
            previous_active = active
        return activation_times

    def _average_temperature_at(
        self,
        temperature_history: dict[str, list[tuple[datetime, float]]],
        when: datetime,
    ) -> float | None:
        """Return the average latest-known room temperature at a point in time."""
        values = []
        for readings in temperature_history.values():
            latest_value = None
            for timestamp, value in readings:
                if timestamp > when:
                    break
                latest_value = value
            if latest_value is not None:
                values.append(latest_value)

        if not values:
            return None
        return sum(values) / len(values)

    async def _async_build_predictive_result(self) -> dict[str, Any]:
        """Build a Predictive Comfort Mode recommendation."""
        comfort_low = self._option_float(
            CONF_PREDICTIVE_COMFORT_LOW,
            DEFAULT_PREDICTIVE_COMFORT_LOW,
        )
        comfort_high = self._option_float(
            CONF_PREDICTIVE_COMFORT_HIGH,
            DEFAULT_PREDICTIVE_COMFORT_HIGH,
        )

        indoor_temperature = self._average_numeric_states(
            self.predictive_temperature_sensors
        )
        if indoor_temperature is None:
            indoor_temperature = self._thermostat_current_temperature()

        outdoor_data = await self._async_get_weather_data()
        current_outdoor_temperature = outdoor_data["current_temperature"]
        forecast_high = outdoor_data["forecast_high"]
        forecast_low = outdoor_data["forecast_low"]
        rainy_forecast = outdoor_data["rainy_forecast"]

        active_activity_entities = self._active_activity_entities()
        humidity = self._average_numeric_states(self.predictive_humidity_sensors)

        if indoor_temperature is None:
            return {
                "mode": PREDICTIVE_MODE_INSUFFICIENT_DATA,
                "reason": "No indoor temperature data is available",
                "reasons": ["No indoor temperature data is available"],
                "auto_adjust_enabled": self.predictive_auto_adjust,
                "allow_hvac_mode_change": self.predictive_allow_hvac_mode_change,
                "allow_away": self.predictive_allow_away,
                "away_mode_active": self.is_away,
                "comfort_low": comfort_low,
                "comfort_high": comfort_high,
                "tracked_temperature_sensors": self.predictive_temperature_sensors,
            }

        if forecast_high is None and current_outdoor_temperature is not None:
            forecast_high = current_outdoor_temperature
        if forecast_low is None and current_outdoor_temperature is not None:
            forecast_low = current_outdoor_temperature

        humidity_effect = self._humidity_effect(humidity)
        activity_effect = self._activity_effect(active_activity_entities)
        rain_cooling = (
            self._option_float(
                CONF_PREDICTIVE_RAIN_COOLING,
                DEFAULT_PREDICTIVE_RAIN_COOLING,
            )
            if rainy_forecast
            else 0.0
        )

        outdoor_influence = self._option_float(
            CONF_PREDICTIVE_OUTDOOR_INFLUENCE,
            DEFAULT_PREDICTIVE_OUTDOOR_INFLUENCE,
        )
        trend_weight = self._option_float(
            CONF_PREDICTIVE_TREND_WEIGHT,
            DEFAULT_PREDICTIVE_TREND_WEIGHT,
        )
        heat_pressure = 0.0
        cool_pressure = 0.0
        trend_pressure = 0.0
        if forecast_high is not None:
            heat_pressure = max(0.0, forecast_high - comfort_high) * outdoor_influence
        if forecast_low is not None:
            cool_pressure = max(0.0, comfort_low - forecast_low) * outdoor_influence
        if current_outdoor_temperature is not None and forecast_high is not None:
            trend_pressure = max(
                0.0,
                forecast_high - current_outdoor_temperature,
            ) * outdoor_influence * trend_weight

        predicted_temperature = (
            indoor_temperature
            + heat_pressure
            + trend_pressure
            + humidity_effect
            + activity_effect
            - cool_pressure
            - rain_cooling
        )

        trigger_margin = self._option_float(
            CONF_PREDICTIVE_TRIGGER_MARGIN,
            DEFAULT_PREDICTIVE_TRIGGER_MARGIN,
        )
        mode = PREDICTIVE_MODE_IDLE
        target_temperature: float | None = None
        target_hvac_mode: str | None = None
        reasons: list[str] = []

        if predicted_temperature > comfort_high + trigger_margin:
            mode = PREDICTIVE_MODE_PRE_COOL
            target_temperature = max(
                comfort_low,
                comfort_high
                - self._option_float(
                    CONF_PREDICTIVE_PRECOOL_OFFSET,
                    DEFAULT_PREDICTIVE_PRECOOL_OFFSET,
                ),
            )
            target_hvac_mode = HVACMode.COOL
            reasons.append(
                f"Predicted indoor temperature {predicted_temperature:.1f}°F exceeds "
                f"comfort high {comfort_high:.1f}°F"
            )
        elif predicted_temperature < comfort_low - trigger_margin:
            mode = PREDICTIVE_MODE_PRE_HEAT
            target_temperature = min(
                comfort_high,
                comfort_low
                + self._option_float(
                    CONF_PREDICTIVE_PREHEAT_OFFSET,
                    DEFAULT_PREDICTIVE_PREHEAT_OFFSET,
                ),
            )
            target_hvac_mode = HVACMode.HEAT
            reasons.append(
                f"Predicted indoor temperature {predicted_temperature:.1f}°F is below "
                f"comfort low {comfort_low:.1f}°F"
            )
        else:
            reasons.append("Predicted indoor temperature remains within comfort band")

        if forecast_high is not None:
            reasons.append(f"Forecast high considered: {forecast_high:.1f}°F")
        if rainy_forecast:
            reasons.append(f"Rain or precipitation cooling applied: {rain_cooling:.1f}°F")
        if humidity_effect:
            reasons.append(f"Humidity heat effect: +{humidity_effect:.1f}°F")
        if activity_effect:
            reasons.append(
                f"Activity heat gain from {len(active_activity_entities)} active "
                f"entity/entities: +{activity_effect:.1f}°F"
            )
        if trend_pressure:
            reasons.append(f"Outdoor warming trend effect: +{trend_pressure:.1f}°F")

        return {
            "mode": mode,
            "reason": reasons[0],
            "reasons": reasons,
            "auto_adjust_enabled": self.predictive_auto_adjust,
            "allow_hvac_mode_change": self.predictive_allow_hvac_mode_change,
            "allow_away": self.predictive_allow_away,
            "away_mode_active": self.is_away,
            "vacation_mode_active": self.vacation_mode_active,
            "vacation_mode_entity": self.vacation_mode_entity,
            "comfort_low": round(comfort_low, 1),
            "comfort_high": round(comfort_high, 1),
            "indoor_temperature": round(indoor_temperature, 1),
            "predicted_temperature": round(predicted_temperature, 1),
            "current_outdoor_temperature": self._round_optional(
                current_outdoor_temperature
            ),
            "forecast_high": self._round_optional(forecast_high),
            "forecast_low": self._round_optional(forecast_low),
            "humidity": self._round_optional(humidity),
            "humidity_effect": round(humidity_effect, 1),
            "activity_effect": round(activity_effect, 1),
            "outdoor_influence": round(outdoor_influence, 2),
            "trend_weight": round(trend_weight, 2),
            "heat_pressure": round(heat_pressure, 1),
            "cool_pressure": round(cool_pressure, 1),
            "trend_pressure": round(trend_pressure, 1),
            "active_activity_entities": active_activity_entities,
            "learning": self.predictive_learning_result,
            "learned_activity_heat_gains": self._activity_heat_gains,
            "rainy_forecast": rainy_forecast,
            "rain_cooling": round(rain_cooling, 1),
            "target_temperature": self._round_optional(target_temperature),
            "target_hvac_mode": target_hvac_mode,
            "weather_entity": self._resolved_predictive_weather_entity(),
            "configured_weather_entity": self.predictive_weather_entity,
            "lookahead_hours": self._option_int(
                CONF_PREDICTIVE_LOOKAHEAD_HOURS,
                DEFAULT_PREDICTIVE_LOOKAHEAD_HOURS,
            ),
            "tracked_temperature_sensors": self.predictive_temperature_sensors,
            "tracked_humidity_sensors": self.predictive_humidity_sensors,
            "tracked_activity_entities": self.predictive_activity_entities,
        }

    async def _async_apply_predictive_result(self, result: dict[str, Any]) -> None:
        """Coordinate a predictive recommendation with the main controller."""
        request = self._predictive_control_request(result)
        result["adjustment_status"] = request["status"]

        if not request["eligible"]:
            return

        result["coordinated_hvac_mode"] = request["hvac_mode"].value
        result["coordinated_target_temperature"] = round(
            request["target_temperature"],
            1,
        )

        thermostat_state = await self.async_update_thermostat_and_vents()
        if thermostat_state is None:
            result["adjustment_status"] = "coordinated_no_state"
            return

        result["thermostat_control_action"] = thermostat_state.recommended_action.value
        result["thermostat_control_reason"] = thermostat_state.action_reason

        if thermostat_state.recommended_action == ThermostatAction.WAIT_CYCLE_OFF:
            result["adjustment_status"] = "coordinated_wait_cycle_off"
            return

        if thermostat_state.recommended_action == ThermostatAction.WAIT_CYCLE_ON:
            result["adjustment_status"] = "coordinated_wait_cycle_on"
            return

        result["adjustment_status"] = "coordinated"

    def _thermostat_current_temperature(self) -> float | None:
        """Return the thermostat current temperature, if numeric."""
        climate_state = self.hass.states.get(self.thermostat)
        if climate_state is None:
            return None
        return self._float_or_none(climate_state.attributes.get("current_temperature"))

    def _average_numeric_states(self, entity_ids: list[str]) -> float | None:
        """Return the average numeric state for available entities."""
        values = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            value = self._float_or_none(state.state)
            if value is not None:
                values.append(value)

        if not values:
            return None
        return sum(values) / len(values)

    def _active_activity_entities(self) -> list[str]:
        """Return configured activity entities that are currently active."""
        active_entities = []
        for entity_id in self.predictive_activity_entities:
            state = self.hass.states.get(entity_id)
            if state is not None and self._is_activity_state_value(state.state):
                active_entities.append(entity_id)
        return active_entities

    def _activity_effect(self, active_activity_entities: list[str]) -> float:
        """Return heat effect from active room entities."""
        if (
            self.predictive_history_learning_enabled
            and self.predictive_learning_result.get("status") == "ready"
        ):
            return sum(
                self._activity_heat_gains.get(entity_id, 0.0)
                for entity_id in active_activity_entities
            )

        return len(active_activity_entities) * self._option_float(
            CONF_PREDICTIVE_ACTIVITY_HEAT_GAIN,
            DEFAULT_PREDICTIVE_ACTIVITY_HEAT_GAIN,
        )

    def _is_activity_state_value(self, value: Any) -> bool:
        """Return whether an activity state should add heat load."""
        if value in (STATE_UNAVAILABLE, STATE_UNKNOWN, None):
            return False

        normalized_state = str(value).lower()
        if normalized_state in ACTIVE_STATES:
            return True
        if normalized_state in ("off", "not_home", "idle", "standby", "locked"):
            return False

        numeric_state = self._float_or_none(value)
        return numeric_state is not None and numeric_state > 0.5

    def _humidity_effect(self, humidity: float | None) -> float:
        """Return perceived heat load from indoor humidity."""
        if humidity is None:
            return 0.0
        return max(0.0, humidity - HUMIDITY_COMFORT_BASELINE) * self._option_float(
            CONF_PREDICTIVE_HUMIDITY_SENSITIVITY,
            DEFAULT_PREDICTIVE_HUMIDITY_SENSITIVITY,
        )

    async def _async_get_weather_data(self) -> dict[str, Any]:
        """Return current and forecast weather values for predictive comfort."""
        weather_entity = self._resolved_predictive_weather_entity()
        state = self.hass.states.get(weather_entity) if weather_entity else None
        current_temperature = None
        forecast: list[dict[str, Any]] = []

        if state is not None:
            current_temperature = self._float_or_none(
                state.attributes.get("temperature")
            )
            forecast = self._extract_forecast_list(state.attributes)

        if not forecast and weather_entity and self.hass.services.has_service(
            WEATHER_DOMAIN,
            SERVICE_GET_FORECASTS,
        ):
            forecast = await self._async_fetch_weather_forecast(weather_entity)

        forecast_items = self._forecast_items_in_lookahead(forecast)
        forecast_temperatures = [
            value
            for item in forecast_items
            if (value := self._float_or_none(item.get("temperature"))) is not None
        ]
        forecast_low_temperatures = [
            value
            for item in forecast_items
            if (value := self._float_or_none(item.get("templow"))) is not None
        ]
        rainy_forecast = any(
            self._forecast_item_is_rainy(item) for item in forecast_items
        )

        return {
            "current_temperature": current_temperature,
            "forecast_high": max(forecast_temperatures)
            if forecast_temperatures
            else None,
            "forecast_low": (
                min(forecast_low_temperatures or forecast_temperatures)
                if (forecast_low_temperatures or forecast_temperatures)
                else None
            ),
            "rainy_forecast": rainy_forecast,
        }

    async def _async_fetch_weather_forecast(
        self,
        weather_entity: str,
    ) -> list[dict[str, Any]]:
        """Fetch hourly forecast data from Home Assistant's weather service."""
        try:
            response = await self.hass.services.async_call(
                WEATHER_DOMAIN,
                SERVICE_GET_FORECASTS,
                {"entity_id": weather_entity, "type": FORECAST_TYPE_HOURLY},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, TypeError) as ex:
            _LOGGER.warning(
                "Failed to fetch weather forecast for predictive comfort from %s: %s",
                weather_entity,
                ex,
            )
            return []

        if not isinstance(response, dict):
            return []

        entity_response = response.get(weather_entity, {})
        if not isinstance(entity_response, dict):
            return []

        forecast = entity_response.get("forecast", [])
        if not isinstance(forecast, list):
            return []

        return [item for item in forecast if isinstance(item, dict)]

    def _extract_forecast_list(self, attributes: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract forecast data from a weather entity's attributes."""
        for key in ("forecast", "forecast_hourly"):
            forecast = attributes.get(key)
            if isinstance(forecast, list):
                return [item for item in forecast if isinstance(item, dict)]
        return []

    def _forecast_items_in_lookahead(
        self,
        forecast: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return forecast entries within the configured lookahead window."""
        if not forecast:
            return []

        now = dt_util.utcnow()
        cutoff = now + timedelta(
            hours=self._option_int(
                CONF_PREDICTIVE_LOOKAHEAD_HOURS,
                DEFAULT_PREDICTIVE_LOOKAHEAD_HOURS,
            )
        )
        matching_items = []

        for item in forecast:
            item_datetime = item.get("datetime")
            if item_datetime is None:
                matching_items.append(item)
                continue

            parsed = dt_util.parse_datetime(item_datetime)
            if parsed is None:
                continue

            parsed_utc = dt_util.as_utc(parsed)
            if now <= parsed_utc <= cutoff:
                matching_items.append(item)

        return matching_items

    def _forecast_item_is_rainy(self, item: dict[str, Any]) -> bool:
        """Return whether a forecast item indicates rain or precipitation."""
        condition = str(item.get("condition", "")).lower()
        precipitation = self._float_or_none(item.get("precipitation")) or 0.0
        precipitation_probability = (
            self._float_or_none(item.get("precipitation_probability")) or 0.0
        )
        return (
            condition in RAINY_CONDITIONS
            or precipitation > 0
            or precipitation_probability >= 50
        )

    def _float_or_none(self, value: Any) -> float | None:
        """Return a value converted to float, or None when not numeric."""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _history_state(self, item: State | dict[str, Any]) -> Any:
        """Return state value from a recorder history item."""
        if isinstance(item, State):
            return item.state
        return item.get("state")

    def _history_timestamp(self, item: State | dict[str, Any]) -> datetime | None:
        """Return UTC timestamp from a recorder history item."""
        if isinstance(item, State):
            return dt_util.as_utc(item.last_changed)

        timestamp = item.get("last_changed") or item.get("last_updated")
        if isinstance(timestamp, (int, float)):
            return dt_util.utc_from_timestamp(timestamp)
        if isinstance(timestamp, str):
            parsed = dt_util.parse_datetime(timestamp)
            if parsed is not None:
                return dt_util.as_utc(parsed)
        return None

    def _round_optional(self, value: float | None) -> float | None:
        """Round a float for attributes while preserving None."""
        if value is None:
            return None
        return round(value, 1)

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

    def _schedule_close_timer(self) -> None:
        """Schedule the close timeout if one is not already pending."""
        if self._close_timer is not None:
            return

        self._close_timer = self.hass.loop.call_later(
            self.close_timeout * 60,
            lambda: self.hass.async_create_task(self._async_close_timeout_expired()),
        )
        _LOGGER.debug("Started close timer for %d minutes", self.close_timeout)

    def reconcile_restored_pause_state(self) -> None:
        """Reconcile restored paused state after entity restore finishes."""
        if self.integration_paused:
            return

        self._update_open_sensors()
        if self.is_paused:
            if not self._open_sensor_times:
                self._schedule_close_timer()
            return

        self._check_initial_open_sensors()

    async def _async_set_hvac_mode(self, hvac_mode: HVACMode | str) -> None:
        """Set the physical thermostat mode and update cycle tracking on success."""
        mode_value = hvac_mode.value if hasattr(hvac_mode, "value") else hvac_mode
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            "set_hvac_mode",
            {
                "entity_id": self.thermostat,
                "hvac_mode": mode_value,
            },
            blocking=True,
        )

        if mode_value == HVACMode.OFF.value:
            self.thermostat_controller.record_thermostat_off()
        else:
            self.thermostat_controller.record_thermostat_on()

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
        if last_known_mode := self._valid_hvac_mode(new_state.state, allow_off=False):
            self._last_known_hvac_mode = last_known_mode
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

        if self.predictive_comfort_enabled and (
            old_state is None
            or old_state.state != new_state.state
            or old_state.attributes.get("current_temperature")
            != new_state.attributes.get("current_temperature")
        ):
            self.hass.async_create_task(self.async_evaluate_predictive_comfort())

        # Handle manual overrides while paused
        if self.is_paused:
            if self._restoring_hvac_mode:
                return

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
            if old_state and old_state.state == STATE_ON:
                self._handle_sensor_closed(entity_id)
                self.async_set_updated_data(None)
            return

        # Ignore unavailable/unknown states
        if new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if old_state and old_state.state == STATE_ON:
                self._handle_sensor_closed(entity_id)
                self.async_set_updated_data(None)
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

        if self.predictive_comfort_enabled:
            self.hass.async_create_task(self.async_evaluate_predictive_comfort())

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
            self._schedule_close_timer()

    async def _async_open_timeout_expired(self) -> None:
        """Handle open timeout expiration - pause the thermostat."""
        if self.integration_paused:
            self._cancel_open_timer()
            return

        # If already paused, nothing to do.
        if self.is_paused:
            self._cancel_open_timer()
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

        # Get current HVAC mode before turning off
        climate_state = self.hass.states.get(self.thermostat)
        previous_hvac_mode = self._capture_previous_hvac_mode()

        try:
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

            await self._async_set_hvac_mode(HVACMode.OFF)
        except Exception:
            _LOGGER.exception("Failed to pause thermostat")
            self.async_set_updated_data(None)
            return

        self.is_paused = True
        self.previous_hvac_mode = previous_hvac_mode
        self.trigger_sensor = trigger_sensor

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

        # Restore previous HVAC mode when it is usable; invalid restored values like
        # unavailable should not keep the integration paused forever.
        resume_hvac_mode = self._resume_hvac_mode()

        try:
            if resume_hvac_mode:
                self._restoring_hvac_mode = True
                await self._async_set_hvac_mode(resume_hvac_mode)
        except Exception:
            _LOGGER.exception("Failed to resume thermostat")
            self._schedule_close_timer()
            self.async_set_updated_data(None)
            return
        finally:
            self._restoring_hvac_mode = False

        self.is_paused = False
        self._set_previous_hvac_mode_after_resume(resume_hvac_mode)

        # Send notification
        await self._async_send_notification(paused=False)

        self.trigger_sensor = None

        # Immediately evaluate thermostat and vent state after resume.
        await self.async_update_thermostat_and_vents()

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

        # Get current HVAC mode before turning off
        previous_hvac_mode = self._capture_previous_hvac_mode()

        self._cancel_open_timer()

        try:
            await self._async_set_hvac_mode(HVACMode.OFF)
        except Exception:
            _LOGGER.exception("Failed to pause thermostat via service")
            self.async_set_updated_data(None)
            return

        self.is_paused = True
        self.previous_hvac_mode = previous_hvac_mode

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

        resume_hvac_mode = self._resume_hvac_mode()

        try:
            if resume_hvac_mode:
                self._restoring_hvac_mode = True
                await self._async_set_hvac_mode(resume_hvac_mode)
        except Exception:
            _LOGGER.exception("Failed to resume thermostat via service")
            self.async_set_updated_data(None)
            return
        finally:
            self._restoring_hvac_mode = False

        # Send notification
        await self._async_send_notification(paused=False)

        self.is_paused = False
        self._set_previous_hvac_mode_after_resume(resume_hvac_mode)
        self.trigger_sensor = None

        # Immediately evaluate thermostat and vent state after resume.
        await self.async_update_thermostat_and_vents()

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
