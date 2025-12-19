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

# Defaults
DEFAULT_OPEN_TIMEOUT = 5  # minutes
DEFAULT_CLOSE_TIMEOUT = 5  # minutes
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
