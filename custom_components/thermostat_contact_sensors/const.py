"""Constants for the Thermostat Contact Sensors integration."""

DOMAIN = "thermostat_contact_sensors"

# Configuration keys
CONF_CONTACT_SENSORS = "contact_sensors"
CONF_THERMOSTAT = "thermostat"
CONF_OPEN_TIMEOUT = "open_timeout"
CONF_CLOSE_TIMEOUT = "close_timeout"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_NOTIFY_TITLE_PAUSED = "notify_title_paused"
CONF_NOTIFY_MESSAGE_PAUSED = "notify_message_paused"
CONF_NOTIFY_TITLE_RESUMED = "notify_title_resumed"
CONF_NOTIFY_MESSAGE_RESUMED = "notify_message_resumed"
CONF_NOTIFICATION_TAG = "notification_tag"
CONF_PREDICTIVE_COMFORT_ENABLED = "predictive_comfort_enabled"
CONF_PREDICTIVE_AUTO_ADJUST = "predictive_auto_adjust"
CONF_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE = "predictive_allow_hvac_mode_change"
CONF_PREDICTIVE_ALLOW_AWAY = "predictive_allow_away"
CONF_PREDICTIVE_WEATHER_ENTITY = "predictive_weather_entity"
CONF_PREDICTIVE_TEMPERATURE_SENSORS = "predictive_temperature_sensors"
CONF_PREDICTIVE_HUMIDITY_SENSORS = "predictive_humidity_sensors"
CONF_PREDICTIVE_ACTIVITY_ENTITIES = "predictive_activity_entities"
CONF_PREDICTIVE_COMFORT_LOW = "predictive_comfort_low"
CONF_PREDICTIVE_COMFORT_HIGH = "predictive_comfort_high"
CONF_PREDICTIVE_LOOKAHEAD_HOURS = "predictive_lookahead_hours"
CONF_PREDICTIVE_TRIGGER_MARGIN = "predictive_trigger_margin"
CONF_PREDICTIVE_PRECOOL_OFFSET = "predictive_precool_offset"
CONF_PREDICTIVE_PREHEAT_OFFSET = "predictive_preheat_offset"
CONF_PREDICTIVE_OUTDOOR_INFLUENCE = "predictive_outdoor_influence"
CONF_PREDICTIVE_TREND_WEIGHT = "predictive_trend_weight"
CONF_PREDICTIVE_HUMIDITY_SENSITIVITY = "predictive_humidity_sensitivity"
CONF_PREDICTIVE_ACTIVITY_HEAT_GAIN = "predictive_activity_heat_gain"
CONF_PREDICTIVE_RAIN_COOLING = "predictive_rain_cooling"
CONF_PREDICTIVE_EVALUATION_INTERVAL = "predictive_evaluation_interval"
CONF_PREDICTIVE_MIN_ADJUSTMENT_INTERVAL = "predictive_min_adjustment_interval"
CONF_PREDICTIVE_HISTORY_LEARNING_ENABLED = "predictive_history_learning_enabled"
CONF_PREDICTIVE_HISTORY_LOOKBACK_DAYS = "predictive_history_lookback_days"
CONF_PREDICTIVE_LEARNING_WINDOW_MINUTES = "predictive_learning_window_minutes"
CONF_PREDICTIVE_LEARNING_REFRESH_INTERVAL = "predictive_learning_refresh_interval"
CONF_PREDICTIVE_MIN_LEARNING_SAMPLES = "predictive_min_learning_samples"
CONF_PREDICTIVE_MEANINGFUL_TEMP_DELTA = "predictive_meaningful_temp_delta"
CONF_PREDICTIVE_MAX_LEARNED_HEAT_GAIN = "predictive_max_learned_heat_gain"

# Area configuration keys
CONF_AREAS = "areas"
CONF_AREA_ID = "area_id"
CONF_AREA_ENABLED = "enabled"
CONF_BINARY_SENSORS = "binary_sensors"
CONF_TEMPERATURE_SENSORS = "temperature_sensors"
CONF_SENSORS = "sensors"
CONF_VENTS = "vents"

# Occupancy configuration keys
CONF_MIN_OCCUPANCY_MINUTES = "min_occupancy_minutes"
CONF_GRACE_PERIOD_MINUTES = "grace_period_minutes"

# Thermostat control configuration keys
CONF_TEMPERATURE_DEADBAND = "temperature_deadband"
CONF_MIN_CYCLE_ON_MINUTES = "min_cycle_on_minutes"
CONF_MIN_CYCLE_OFF_MINUTES = "min_cycle_off_minutes"
CONF_UNOCCUPIED_HEATING_THRESHOLD = "unoccupied_heating_threshold"
CONF_UNOCCUPIED_COOLING_THRESHOLD = "unoccupied_cooling_threshold"
CONF_HEATING_BOOST_OFFSET = "heating_boost_offset"
CONF_COOLING_BOOST_OFFSET = "cooling_boost_offset"

# Vent control configuration keys (global)
CONF_MIN_VENTS_OPEN = "min_vents_open"
CONF_VENT_OPEN_DELAY_SECONDS = "vent_open_delay_seconds"
CONF_VENT_DEBOUNCE_SECONDS = "vent_debounce_seconds"

# Vent control configuration keys (per-area overrides)
CONF_AREA_MIN_VENTS_OPEN = "area_min_vents_open"
CONF_AREA_VENT_OPEN_DELAY_SECONDS = "area_vent_open_delay_seconds"

# Per-area critical temperature override
CONF_AREA_FORCE_TRACK_WHEN_CRITICAL = "force_track_when_critical"
CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED = "track_only_when_occupied"

# Eco Mode critical temperature tracking options
CONF_ECO_MODE_CRITICAL_TRACKING = "eco_mode_critical_tracking"
ECO_CRITICAL_NONE = "do_not_track_critical"
ECO_CRITICAL_SELECT = "track_select_critical"
ECO_CRITICAL_ALL = "track_all_critical"
# Default should preserve legacy behavior (Eco Mode switch default OFF):
# track critical temperatures even when rooms are inactive.
DEFAULT_ECO_MODE_CRITICAL_TRACKING = ECO_CRITICAL_SELECT

# User override behavior
CONF_RESPECT_USER_OFF = "respect_user_off"

# Tracked rooms configuration keys
CONF_ONLY_TRACK_SELECTED_ROOMS = "only_track_selected_rooms"
CONF_TRACKED_ROOMS = "tracked_rooms"  # Set of area_ids that are being tracked

# Away mode configuration keys
CONF_AWAY_PRESENCE_ENTITY = "away_presence_entity"
CONF_AWAY_HEAT_TEMP_DIFF = "away_heat_temp_diff"
CONF_AWAY_COOL_TEMP_DIFF = "away_cool_temp_diff"

# Defaults
DEFAULT_OPEN_TIMEOUT = 5  # minutes
DEFAULT_CLOSE_TIMEOUT = 5  # minutes
DEFAULT_MIN_OCCUPANCY_MINUTES = 5  # minutes
DEFAULT_GRACE_PERIOD_MINUTES = 5  # minutes (minimum 2)
DEFAULT_TEMPERATURE_DEADBAND = 0.5  # degrees (precision: 0.1)
DEFAULT_MIN_CYCLE_ON_MINUTES = 5  # minutes
DEFAULT_MIN_CYCLE_OFF_MINUTES = 5  # minutes
DEFAULT_UNOCCUPIED_HEATING_THRESHOLD = 3.0  # degrees below heat target
DEFAULT_UNOCCUPIED_COOLING_THRESHOLD = 3.0  # degrees above cool target
DEFAULT_HEATING_BOOST_OFFSET = 0.0  # degrees to boost heat setpoint
DEFAULT_COOLING_BOOST_OFFSET = 0.0  # degrees to boost cool setpoint
DEFAULT_MIN_VENTS_OPEN = 5  # minimum number of vents that must remain open
DEFAULT_VENT_OPEN_DELAY_SECONDS = 30  # seconds after occupancy before vents open
DEFAULT_VENT_DEBOUNCE_SECONDS = 30  # seconds between vent state changes
DEFAULT_AWAY_HEAT_TEMP_DIFF = -3.0  # degrees to lower heat target when away
DEFAULT_AWAY_COOL_TEMP_DIFF = 3.0  # degrees to raise cool target when away
DEFAULT_NOTIFY_TITLE_PAUSED = "Thermostat · Paused"
DEFAULT_NOTIFY_MESSAGE_PAUSED = (
    "{{ trigger_sensor_name }} has been open for {{ open_timeout }} minutes. "
    "Thermostat will shut down until all doors and windows have been closed."
)
DEFAULT_NOTIFY_TITLE_RESUMED = "Thermostat · Resumed"
DEFAULT_NOTIFY_MESSAGE_RESUMED = (
    "All doors and windows have been closed for {{ close_timeout }} minutes. "
    "Thermostat will resume normal operation (restored to {{ previous_mode }} mode)."
)
DEFAULT_NOTIFICATION_TAG = "thermostat_contact_sensors_notification"
DEFAULT_RESPECT_USER_OFF = False  # Default: integration will always resume thermostat
DEFAULT_PREDICTIVE_COMFORT_ENABLED = False
DEFAULT_PREDICTIVE_AUTO_ADJUST = False
DEFAULT_PREDICTIVE_ALLOW_HVAC_MODE_CHANGE = False
DEFAULT_PREDICTIVE_ALLOW_AWAY = False
DEFAULT_PREDICTIVE_COMFORT_LOW = 71.0
DEFAULT_PREDICTIVE_COMFORT_HIGH = 74.0
DEFAULT_PREDICTIVE_LOOKAHEAD_HOURS = 6
DEFAULT_PREDICTIVE_TRIGGER_MARGIN = 0.5
DEFAULT_PREDICTIVE_PRECOOL_OFFSET = 2.0
DEFAULT_PREDICTIVE_PREHEAT_OFFSET = 1.0
DEFAULT_PREDICTIVE_OUTDOOR_INFLUENCE = 0.10
DEFAULT_PREDICTIVE_TREND_WEIGHT = 0.75
DEFAULT_PREDICTIVE_HUMIDITY_SENSITIVITY = 0.05
DEFAULT_PREDICTIVE_ACTIVITY_HEAT_GAIN = 1.0
DEFAULT_PREDICTIVE_RAIN_COOLING = 2.0
DEFAULT_PREDICTIVE_EVALUATION_INTERVAL = 15
DEFAULT_PREDICTIVE_MIN_ADJUSTMENT_INTERVAL = 45
DEFAULT_PREDICTIVE_HISTORY_LEARNING_ENABLED = True
DEFAULT_PREDICTIVE_HISTORY_LOOKBACK_DAYS = 7
DEFAULT_PREDICTIVE_LEARNING_WINDOW_MINUTES = 90
DEFAULT_PREDICTIVE_LEARNING_REFRESH_INTERVAL = 360
DEFAULT_PREDICTIVE_MIN_LEARNING_SAMPLES = 3
DEFAULT_PREDICTIVE_MEANINGFUL_TEMP_DELTA = 0.5
DEFAULT_PREDICTIVE_MAX_LEARNED_HEAT_GAIN = 5.0
PREDICTIVE_MODE_DISABLED = "disabled"
PREDICTIVE_MODE_IDLE = "idle"
PREDICTIVE_MODE_PRE_COOL = "pre_cool"
PREDICTIVE_MODE_PRE_HEAT = "pre_heat"
PREDICTIVE_MODE_INSUFFICIENT_DATA = "insufficient_data"

# Platforms
PLATFORMS = ["binary_sensor", "climate", "select", "sensor", "switch"]
