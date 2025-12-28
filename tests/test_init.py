"""Tests for integration setup and lifecycle."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors.const import (
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_NOTIFICATION_TAG,
    CONF_NOTIFY_MESSAGE_PAUSED,
    CONF_NOTIFY_MESSAGE_RESUMED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE_PAUSED,
    CONF_NOTIFY_TITLE_RESUMED,
    CONF_OPEN_TIMEOUT,
    CONF_THERMOSTAT,
    DOMAIN,
)

from .conftest import TEST_SENSOR_1, TEST_THERMOSTAT


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test successful setup of config entry."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry.state == ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test successful unload of config entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.LOADED

    result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED


async def test_reload_entry(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test reload of config entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Unload
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state == ConfigEntryState.NOT_LOADED

    # Reload
    result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert mock_config_entry.state == ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_runtime_data_created(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that runtime data is created on setup."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data is not None
    assert hasattr(mock_config_entry.runtime_data, "is_paused")
    assert hasattr(mock_config_entry.runtime_data, "open_sensors")

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_platforms_setup(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that all platforms are set up."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check binary_sensor platform
    binary_sensor_entity = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"
    assert hass.states.get(binary_sensor_entity) is not None

    # Check sensor platform
    sensor_entity = f"sensor.test_thermostat_contact_sensors_open_sensors"
    assert hass.states.get(sensor_entity) is not None

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_device_created(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that device is created."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)

    # Find our device
    our_device = None
    for device in device_registry.devices.values():
        if (DOMAIN, mock_config_entry.entry_id) in device.identifiers:
            our_device = device
            break

    assert our_device is not None
    assert our_device.manufacturer == "Custom Integration"
    assert our_device.model == "Thermostat Contact Sensors"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_options_update_listener(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that options update listener works."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    original_timeout = coordinator.open_timeout

    # Update options
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, "open_timeout": 20},
    )
    await hass.async_block_till_done()

    # The options update listener reloads the integration, so we need
    # to get the new coordinator instance
    coordinator = mock_config_entry.runtime_data

    # Verify coordinator was updated with new options
    assert coordinator.open_timeout == 20
    assert coordinator.open_timeout != original_timeout

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_multiple_config_entries(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test multiple config entries can be set up."""
    # Set up a second thermostat for a second entry
    hass.states.async_set(
        "climate.second_thermostat",
        "cool",
        {"friendly_name": "Second Thermostat"},
    )
    await hass.async_block_till_done()

    # Create a second config entry for the second thermostat
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Second Thermostat Config",
        data={
            "name": "Second Thermostat Config",
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1],
            CONF_THERMOSTAT: "climate.second_thermostat",
        },
        options={
            CONF_OPEN_TIMEOUT: 2,
            CONF_CLOSE_TIMEOUT: 2,
            CONF_NOTIFY_SERVICE: "",
            CONF_NOTIFY_TITLE_PAUSED: "Paused",
            CONF_NOTIFY_MESSAGE_PAUSED: "Paused",
            CONF_NOTIFY_TITLE_RESUMED: "Resumed",
            CONF_NOTIFY_MESSAGE_RESUMED: "Resumed",
            CONF_NOTIFICATION_TAG: "tag",
        },
        entry_id="second_entry_id",
        unique_id="climate.second_thermostat",
    )

    # Add first entry and set it up
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state == ConfigEntryState.LOADED

    # Add and set up second entry separately
    second_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(second_entry.entry_id)
    await hass.async_block_till_done()
    assert second_entry.state == ConfigEntryState.LOADED

    # Verify both entries have their runtime_data
    assert mock_config_entry.runtime_data is not None
    assert second_entry.runtime_data is not None

    # Cleanup
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.config_entries.async_unload(second_entry.entry_id)


async def test_coordinator_cleanup_on_unload(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that coordinator resources are cleaned up on unload."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data

    # Verify coordinator is set up
    assert coordinator._unsub_state_change is not None

    # Unload
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Verify cleanup
    assert coordinator._unsub_state_change is None
