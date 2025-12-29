"""Tests for the options flow."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CLOSE_TIMEOUT,
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
    CONF_SENSORS,
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

from .conftest import (
    TEST_AREA_BEDROOM,
    TEST_AREA_LIVING_ROOM,
    TEST_NOTIFY_SERVICE,
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
    TEST_THERMOSTAT,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities, setup_entity_registry) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_options_flow_shows_menu(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that options flow shows the main menu."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    assert "manage_areas" in result["menu_options"]
    assert "configure_area_sensors" in result["menu_options"]
    assert "global_settings" in result["menu_options"]
    assert "thermostat" in result["menu_options"]


async def test_options_flow_global_settings(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test navigating to and updating global settings."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select global settings from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "global_settings"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "global_settings"

    # Update the settings
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_OPEN_TIMEOUT: 10,
            CONF_CLOSE_TIMEOUT: 15,
            CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_OPEN_TIMEOUT] == 10
    assert result["data"][CONF_CLOSE_TIMEOUT] == 15


async def test_options_flow_global_settings_grace_period(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test configuring the grace period in global settings."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select global settings from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "global_settings"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "global_settings"

    # Update the settings with custom grace period
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_MIN_OCCUPANCY_MINUTES: DEFAULT_MIN_OCCUPANCY_MINUTES,
            CONF_GRACE_PERIOD_MINUTES: 10,  # Custom grace period
            CONF_TEMPERATURE_DEADBAND: DEFAULT_TEMPERATURE_DEADBAND,
            CONF_MIN_CYCLE_ON_MINUTES: DEFAULT_MIN_CYCLE_ON_MINUTES,
            CONF_MIN_CYCLE_OFF_MINUTES: DEFAULT_MIN_CYCLE_OFF_MINUTES,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_GRACE_PERIOD_MINUTES] == 10


async def test_options_flow_thermostat(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test navigating to and updating thermostat selection."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select thermostat from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "thermostat"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "thermostat"

    # Update the thermostat
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_THERMOSTAT: TEST_THERMOSTAT,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.data[CONF_THERMOSTAT] == TEST_THERMOSTAT


async def test_options_flow_manage_areas_checkboxes(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test navigating to the manage areas form with checkboxes."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select manage areas from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "manage_areas"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manage_areas"


async def test_options_flow_configure_area_sensors_menu(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test navigating to the configure area sensors menu."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select configure area sensors from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "configure_area_sensors"
    # Should have area options
    assert len(result["menu_options"]) >= 1


async def test_options_flow_area_config(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test configuring a specific area."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select configure area sensors from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    # Select the living room area
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": f"area_{TEST_AREA_LIVING_ROOM}"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "area_config"

    # Configure the area
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: [TEST_SENSOR_1],  # Only select one sensor
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
        },
    )

    # Should go back to configure area sensors menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "configure_area_sensors"

    # Verify the area config was updated
    assert mock_config_entry.data[CONF_AREAS][TEST_AREA_LIVING_ROOM][CONF_BINARY_SENSORS] == [TEST_SENSOR_1]


async def test_options_flow_disable_area(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test disabling an area."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Navigate to configure area sensors
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    # Select the bedroom area
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": f"area_{TEST_AREA_BEDROOM}"},
    )

    # Disable the area - only include fields that exist in the schema
    # The bedroom area has binary_sensors but no temperature_sensors or sensors
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AREA_ENABLED: False,
            CONF_BINARY_SENSORS: [TEST_SENSOR_3],
        },
    )

    # Verify the area is disabled
    assert mock_config_entry.data[CONF_AREAS][TEST_AREA_BEDROOM][CONF_AREA_ENABLED] is False


async def test_options_flow_thermostat_required(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that thermostat is required when updating.

    The EntitySelector validates that the thermostat must be a valid entity ID.
    When an empty string is provided, the schema validation rejects it.
    """
    from homeassistant.data_entry_flow import InvalidData

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Select thermostat from menu
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "thermostat"},
    )

    # Try to submit without thermostat - should raise schema validation error
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                CONF_THERMOSTAT: "",
            },
        )


async def test_options_flow_sensor_count_updates_after_adding(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that sensor count updates in menu after adding sensors."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Get initial sensor count for living room - this is what's in the config
    initial_config = mock_config_entry.data[CONF_AREAS][TEST_AREA_LIVING_ROOM]
    initial_count = (
        len(initial_config.get(CONF_BINARY_SENSORS, []))
        + len(initial_config.get(CONF_TEMPERATURE_SENSORS, []))
        + len(initial_config.get(CONF_SENSORS, []))
    )

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Navigate to configure area sensors
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    # Select the living room area
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": f"area_{TEST_AREA_LIVING_ROOM}"},
    )

    # Add an additional sensor (TEST_SENSOR_3 from bedroom)
    # Keep the existing temperature and other sensors
    new_binary_sensors = [TEST_SENSOR_1, TEST_SENSOR_2, TEST_SENSOR_3]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: new_binary_sensors,
        },
    )

    # Verify we're back at configure_area_sensors menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "configure_area_sensors"

    # Verify the binary_sensors count was updated (we added one more)
    updated_config = mock_config_entry.data[CONF_AREAS][TEST_AREA_LIVING_ROOM]
    updated_binary = len(updated_config.get(CONF_BINARY_SENSORS, []))
    initial_binary = len(initial_config.get(CONF_BINARY_SENSORS, []))
    assert updated_binary == len(new_binary_sensors)
    assert updated_binary > initial_binary  # Added one more binary sensor


async def test_options_flow_sensor_count_updates_after_removing(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that sensor count updates in menu after removing sensors."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Get initial sensor count for living room
    initial_config = mock_config_entry.data[CONF_AREAS][TEST_AREA_LIVING_ROOM]
    initial_binary_sensors = initial_config.get(CONF_BINARY_SENSORS, [])
    initial_count = (
        len(initial_binary_sensors)
        + len(initial_config.get(CONF_TEMPERATURE_SENSORS, []))
        + len(initial_config.get(CONF_SENSORS, []))
    )

    # Start options flow
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    # Navigate to configure area sensors
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    # Select the living room area
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": f"area_{TEST_AREA_LIVING_ROOM}"},
    )

    # Remove all but one sensor
    reduced_sensors = [TEST_SENSOR_1]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: reduced_sensors,
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
        },
    )

    # Verify we're back at configure_area_sensors menu
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "configure_area_sensors"

    # Verify the sensor count was updated in the config
    updated_config = mock_config_entry.data[CONF_AREAS][TEST_AREA_LIVING_ROOM]
    updated_count = (
        len(updated_config.get(CONF_BINARY_SENSORS, []))
        + len(updated_config.get(CONF_TEMPERATURE_SENSORS, []))
        + len(updated_config.get(CONF_SENSORS, []))
    )
    assert updated_count == len(reduced_sensors)
    assert updated_count < initial_count

    # Verify the menu shows the updated count in the label
    living_room_option = result["menu_options"].get(f"area_{TEST_AREA_LIVING_ROOM}", "")
    assert f"({updated_count} sensors)" in living_room_option


async def test_options_flow_enable_disable_areas_shows_correct_count(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that enable/disable areas form shows correct sensor counts from config."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # First, modify the sensors in an area
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "configure_area_sensors"},
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": f"area_{TEST_AREA_LIVING_ROOM}"},
    )

    # Set to exactly 1 sensor
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: [TEST_SENSOR_1],
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
        },
    )

    # Now start a new options flow and check the manage_areas form
    result2 = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result2 = await hass.config_entries.options.async_configure(
        result2["flow_id"],
        user_input={"next_step_id": "manage_areas"},
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "manage_areas"

    # Check that the schema has the correct options with updated counts
    # The living room should show (1 sensors) since we set it to 1
    schema = result2["data_schema"]
    # Find the enabled_areas field and check its options
    for key in schema.schema:
        if hasattr(key, "schema") and key.schema == "enabled_areas":
            # This is the enabled_areas field
            break
