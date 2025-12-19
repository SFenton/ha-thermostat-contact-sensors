# Thermostat Contact Sensors

A Home Assistant custom integration that automatically pauses your thermostat when doors or windows are left open, and resumes normal operation when they're closed.

## Features

- **Multi-sensor monitoring**: Select any number of door/window contact sensors
- **Configurable timeouts**: Set custom delays for both open and close events
- **Customizable notifications**: Template-based messages with dynamic variables
- **State tracking**: Exposes sensors showing current state and open sensor count
- **Automatic HVAC restoration**: Remembers and restores the previous HVAC mode

## Installation

### Manual Installation

1. Copy the `custom_components/thermostat_contact_sensors` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration**
4. Search for "Thermostat Contact Sensors"

### HACS Installation (Future)

This integration can be added as a custom repository in HACS.

## Configuration

### Initial Setup

1. **Name**: A friendly name for this configuration
2. **Contact Sensors**: Select all door/window sensors to monitor
3. **Thermostat**: Select the climate entity to control
4. **Open Timeout**: Minutes a sensor must be open before pausing (default: 5)
5. **Close Timeout**: Minutes all sensors must be closed before resuming (default: 5)
6. **Notification Service**: (Optional) Service like `notify.mobile_app_phone`

### Options (Reconfigurable)

After setup, you can adjust:

- Timeouts
- Notification service
- Notification titles and messages (with templates)
- Notification tag for persistent notifications

## Template Variables

Use these in notification messages:

| Variable | Description |
|----------|-------------|
| `{{ trigger_sensor }}` | Entity ID of the triggering sensor |
| `{{ trigger_sensor_name }}` | Friendly name of the triggering sensor |
| `{{ open_sensors }}` | List of open sensor entity IDs |
| `{{ open_sensor_names }}` | List of open sensor friendly names |
| `{{ open_count }}` | Number of currently open sensors |
| `{{ open_doors }}` | Number of open door sensors |
| `{{ open_windows }}` | Number of open window sensors |
| `{{ open_timeout }}` | Configured open timeout (minutes) |
| `{{ close_timeout }}` | Configured close timeout (minutes) |
| `{{ previous_mode }}` | Previous HVAC mode before pausing |
| `{{ thermostat }}` | Thermostat entity ID |

### Example Messages

**Paused:**
```
{{ trigger_sensor_name }} has been open for {{ open_timeout }} minutes. 
Thermostat paused until all {{ open_count }} sensors are closed.
```

**Resumed:**
```
All doors and windows closed for {{ close_timeout }} minutes. 
Thermostat restored to {{ previous_mode }} mode.
```

## Exposed Entities

For each configuration, the integration creates:

### Binary Sensor: Thermostat Paused
- **State**: `on` when thermostat is paused, `off` when running normally
- **Attributes**:
  - `thermostat`: The controlled thermostat entity
  - `previous_mode`: HVAC mode before pausing
  - `open_count`: Current count of open sensors
  - `triggered_by`: Sensor that triggered the pause

### Sensor: Open Sensors
- **State**: Count of currently open sensors
- **Attributes**:
  - `open_sensors`: List of open sensor entity IDs
  - `open_sensor_names`: List of open sensor friendly names
  - `open_doors`: Count of open door sensors
  - `open_windows`: Count of open window sensors
  - `monitored_sensors`: List of all monitored sensors
  - `total_monitored`: Total number of monitored sensors

## How It Works

1. When a monitored contact sensor opens, a timer starts (open timeout)
2. If the sensor closes before timeout, the timer is cancelled
3. If any sensor remains open after timeout, the thermostat is turned off
4. The integration monitors all sensors, waiting for all to close
5. When all sensors have been closed for the close timeout, the thermostat is restored to its previous mode
6. Notifications are sent at pause and resume (if configured)

## Multiple Thermostats

You can add multiple configurations to control different thermostats with different sensor groups. Each configuration operates independently.

## Troubleshooting

### Thermostat not pausing
- Check that contact sensors report `on` when open
- Verify the open timeout hasn't been set too long
- Check Home Assistant logs for errors

### Notifications not working
- Verify the notification service name is correct
- Test the notification service manually first
- Check for template errors in the message

### Previous mode not restoring
- Ensure the thermostat supports the previous HVAC mode
- Check if the thermostat was manually changed while paused

## License

MIT License - See LICENSE file for details.
