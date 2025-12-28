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

# Vent control configuration keys (global)
CONF_MIN_VENTS_OPEN = "min_vents_open"
CONF_VENT_OPEN_DELAY_SECONDS = "vent_open_delay_seconds"
CONF_VENT_DEBOUNCE_SECONDS = "vent_debounce_seconds"

# Vent control configuration keys (per-area overrides)
CONF_AREA_MIN_VENTS_OPEN = "area_min_vents_open"
CONF_AREA_VENT_OPEN_DELAY_SECONDS = "area_vent_open_delay_seconds"

# User override behavior
CONF_RESPECT_USER_OFF = "respect_user_off"

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
DEFAULT_MIN_VENTS_OPEN = 5  # minimum number of vents that must remain open
DEFAULT_VENT_OPEN_DELAY_SECONDS = 30  # seconds after occupancy before vents open
DEFAULT_VENT_DEBOUNCE_SECONDS = 30  # seconds between vent state changes
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

# Platforms
PLATFORMS = ["binary_sensor", "sensor", "switch"]
