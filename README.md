# Thermostat Contact Sensors

A comprehensive Home Assistant custom integration that provides intelligent HVAC control based on contact sensors, room occupancy, temperature readings, and smart vent management.

## Features

### Contact Sensor Monitoring
- **Multi-sensor monitoring**: Select any number of door/window contact sensors
- **Configurable timeouts**: Set custom delays for both open and close events
- **Customizable notifications**: Template-based messages with dynamic variables
- **State tracking**: Exposes sensors showing current state and open sensor count
- **Automatic HVAC restoration**: Remembers and restores the previous HVAC mode
- **Manual override detection**: Respects when users manually control the thermostat

### Room Occupancy Tracking
- **Area-based configuration**: Automatically discovers sensors assigned to Home Assistant areas
- **Multi-sensor occupancy**: Any sensor indicating presence marks the room as occupied (OR logic)
- **Minimum occupancy time**: Rooms must be occupied for a configurable duration before becoming "active" for climate control
- **Grace period**: Active rooms don't immediately deactivate when unoccupied—prevents rapid cycling
- **State persistence**: Occupancy state survives Home Assistant restarts
- **Sensor types**: Supports both `binary_sensor` and `sensor` entities (via `previous_valid_state` attribute)

### Intelligent Thermostat Control
- **Temperature satiation**: Rooms are "satiated" when temperature reaches target ± deadband
- **Multi-mode support**: Works with HEAT, COOL, and HEAT_COOL (auto) modes
- **Cycle protection**: Minimum on/off times to protect HVAC equipment from rapid cycling
- **Critical room protection**: Unoccupied rooms can still trigger HVAC if temperature falls too far from target

### Smart Vent Control
- **Automatic vent management**: Controls cover entities (vents) based on occupancy and temperature
- **Minimum vents open**: Safety feature to prevent HVAC back-pressure by keeping a minimum number of vents open
- **Priority-based selection**: Critical rooms > Active rooms > Occupied rooms > Rooms furthest from target
- **Per-area delays**: Configure custom vent open delays for specific areas
- **Debounce protection**: Prevents rapid open/close cycling of vents
- **Group support**: Properly counts and controls vent groups

## Installation

### Manual Installation

1. Copy the `custom_components/thermostat_contact_sensors` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration**
4. Search for "Thermostat Contact Sensors"

### HACS Installation

1. Open HACS in your Home Assistant instance
2. Click the three dots menu in the top right and select "Custom repositories"
3. Add this repository URL with category "Integration"
4. Click "Install" and restart Home Assistant

## Configuration

### Initial Setup

When adding the integration, you'll configure:

1. **Name**: A friendly name for this configuration
2. **Thermostat**: Select the climate entity to control
3. **Open Timeout**: Minutes a sensor must be open before pausing (default: 5)
4. **Close Timeout**: Minutes all sensors must be closed before resuming (default: 5)
5. **Notification Service**: (Optional) Service like `notify.mobile_app_phone`

After initial setup, all areas with sensors are automatically discovered and enabled.

### Options (Reconfigurable)

After setup, access the integration options to configure:

#### Area Management
- Enable/disable specific areas
- Select which sensors to use for each area
- Configure temperature sensors and vents per area
- Set per-area vent open delays

#### Global Settings

| Setting | Default | Description |
|---------|---------|-------------|
| **Minimum Occupancy Time** | 5 min | Time a room must be occupied before becoming active for climate control |
| **Grace Period** | 5 min | Time an active room stays active after becoming unoccupied (min: 2) |
| **Temperature Deadband** | 0.5° | Temperature tolerance—room is satiated when within this range of target |
| **Min Cycle On Time** | 5 min | Minimum time thermostat must stay ON (protects HVAC equipment) |
| **Min Cycle Off Time** | 5 min | Minimum time thermostat must stay OFF (protects HVAC equipment) |
| **Unoccupied Heating Threshold** | 3.0° | Degrees below heat target that triggers heating in unoccupied rooms |
| **Unoccupied Cooling Threshold** | 3.0° | Degrees above cool target that triggers cooling in unoccupied rooms |
| **Minimum Vents Open** | 5 | Minimum number of vents that must remain open to prevent HVAC back-pressure |
| **Vent Open Delay** | 30 sec | Seconds after occupancy before vents open (prevents false triggers) |
| **Vent Debounce Time** | 30 sec | Minimum time between vent state changes |

#### Notification Settings
- Notification service selection
- Customizable titles and messages with Jinja2 templates
- Notification tag for persistent/replaceable notifications

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
| `{{ thermostat_name }}` | Thermostat friendly name |

### Example Messages

**Paused:**
```jinja2
{{ trigger_sensor_name }} has been open for {{ open_timeout }} minutes. 
Thermostat paused until all {{ open_count }} sensors are closed.
```

**Resumed:**
```jinja2
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

### Sensor: Room Occupancy (per area)
- **State**: `active`, `occupied`, or `inactive`
- **Attributes**:
  - `is_occupied`: Whether room is currently occupied
  - `is_active`: Whether room is active for climate control
  - `occupancy_duration_minutes`: How long the room has been occupied
  - `occupied_sensor_count`: Number of sensors indicating occupancy
  - `total_sensor_count`: Total occupancy sensors in this area

### Sensor: Thermostat Control
- **State**: Current thermostat control status
- **Attributes**: Details about satiation state, active rooms, and recommended actions

### Switch: Respect User Off
- **When OFF** (default): Integration will always turn thermostat back on when windows close
- **When ON**: Integration respects user's choice—if thermostat was off before pause, it stays off

## Services

The integration provides the following services:

### `thermostat_contact_sensors.pause`
Manually pause a thermostat.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `entry_id` | string | Yes | Config entry ID of the integration |

### `thermostat_contact_sensors.resume`
Manually resume a paused thermostat.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `entry_id` | string | Yes | Config entry ID of the integration |

### `thermostat_contact_sensors.recalculate`
Force recalculation of thermostat state and vent positions.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `entry_id` | string | Yes | Config entry ID of the integration |

## How It Works

### Contact Sensor Logic
1. When a monitored contact sensor opens, a timer starts (open timeout)
2. If the sensor closes before timeout, the timer is cancelled
3. If any sensor remains open after timeout, the thermostat is turned off
4. The integration monitors all sensors, waiting for all to close
5. When all sensors have been closed for the close timeout, the thermostat is restored
6. Notifications are sent at pause and resume (if configured)

### Occupancy-Based Climate Control
1. Motion/occupancy sensors in each area track room occupancy
2. A room becomes "active" after being continuously occupied for the minimum occupancy time
3. Active rooms are evaluated for temperature satiation (is the temperature at target?)
4. The thermostat runs when any active room is not satiated
5. When all active rooms are satiated, the thermostat can turn off (respecting cycle protection)
6. If an active room becomes unoccupied, it stays active during the grace period

### Vent Control Logic
1. Vents in occupied/active rooms open after the vent open delay
2. Vents in satiated or inactive rooms close
3. The system ensures at least N vents remain open (minimum vents open setting)
4. Priority determines which vents stay open: critical > active > occupied > distance from target
5. Debounce protection prevents rapid vent changes

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
- Check the "Respect User Off" switch setting

### Rooms not becoming active
- Verify occupancy sensors are reporting correctly
- Check the minimum occupancy time setting
- Ensure the area is enabled in options

### Vents not responding
- Verify cover entities support tilt commands
- Check the vent open delay setting
- Look at the vent debounce time if changes seem delayed

## Diagnostics

This integration supports Home Assistant diagnostics. When reporting issues, download the diagnostics from **Settings → Devices & Services → Thermostat Contact Sensors → ⋮ → Download diagnostics**.

## Development

### Running Tests

Tests can be run locally on Linux/macOS/Windows.

**Using GitHub Actions (recommended):**
```bash
git push  # Tests run automatically via .github/workflows/tests.yml
```

**Using WSL (Ubuntu):**
```bash
wsl -d Ubuntu
cd /mnt/c/path/to/ha-thermostat-contact-sensors
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
pytest tests/ -v --tb=short
```

**Using Windows (PowerShell, Python 3.12 recommended):**

Some Home Assistant test dependencies may fail to build on Windows for newer Python versions (e.g., 3.13). If you hit install errors, use Python 3.12.

```powershell
cd C:\Users\sfent\Repos\ha-thermostat-contact-sensors

# Create/activate a venv (or use your existing one)
py -3.12 -m venv .venv_py312
& .\.venv_py312\Scripts\Activate.ps1

python -m pip install -r requirements_test.txt
python -m pytest -q

# Targeted run example
python -m pytest -q tests/test_climate.py::TestHvacAction
```

**Using Docker:**
```bash
docker run --rm -v "$(pwd):/app" -w /app python:3.12 bash -c \
  "pip install -r requirements_test.txt && pytest tests/ -v"
```

## License

MIT License - See [LICENSE](LICENSE) file for details.
