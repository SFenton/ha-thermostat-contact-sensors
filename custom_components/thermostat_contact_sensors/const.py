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

# Occupancy configuration keys
CONF_MIN_OCCUPANCY_MINUTES = "min_occupancy_minutes"
CONF_GRACE_PERIOD_MINUTES = "grace_period_minutes"

# Thermostat control configuration keys
CONF_TEMPERATURE_DEADBAND = "temperature_deadband"
CONF_MIN_CYCLE_ON_MINUTES = "min_cycle_on_minutes"
CONF_MIN_CYCLE_OFF_MINUTES = "min_cycle_off_minutes"

# Defaults
DEFAULT_OPEN_TIMEOUT = 5  # minutes
DEFAULT_CLOSE_TIMEOUT = 5  # minutes
DEFAULT_MIN_OCCUPANCY_MINUTES = 5  # minutes
DEFAULT_GRACE_PERIOD_MINUTES = 5  # minutes (minimum 2)
DEFAULT_TEMPERATURE_DEADBAND = 0.5  # degrees (precision: 0.1)
DEFAULT_MIN_CYCLE_ON_MINUTES = 5  # minutes
DEFAULT_MIN_CYCLE_OFF_MINUTES = 5  # minutes
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

# Platforms
PLATFORMS = ["binary_sensor", "sensor"]
