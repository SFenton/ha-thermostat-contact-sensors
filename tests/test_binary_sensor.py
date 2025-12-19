"""Tests for the binary sensor platform."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.thermostat_contact_sensors.const import (
    CONF_OPEN_TIMEOUT,
    DOMAIN,
)

from .conftest import TEST_SENSOR_1, TEST_THERMOSTAT


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_binary_sensor_creation(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test binary sensor entity is created."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == STATE_OFF

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_unique_id(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test binary sensor has correct unique ID."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"
    entry = entity_registry.async_get(entity_id)

    assert entry is not None
    assert entry.unique_id == f"{mock_config_entry.entry_id}_paused"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_state_when_paused(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
    mock_notify_service,
) -> None:
    """Test binary sensor shows ON when thermostat is paused."""
    # Modify to use short timeout
    mock_config_entry.add_to_hass(hass)

    # Update options for short timeout
    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_OPEN_TIMEOUT: 0.01},
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"

    # Initially not paused
    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF

    # Open a sensor
    hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
    await hass.async_block_till_done()

    # Wait for timeout
    await asyncio.sleep(1)
    await hass.async_block_till_done()

    # Should be paused now
    state = hass.states.get(entity_id)
    assert state.state == STATE_ON

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_attributes(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test binary sensor attributes."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"
    state = hass.states.get(entity_id)

    assert state.attributes.get("thermostat") == TEST_THERMOSTAT
    assert state.attributes.get("open_count") == 0

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_triggered_by_attribute(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
    mock_notify_service,
) -> None:
    """Test binary sensor shows triggered_by attribute when paused."""
    mock_config_entry.add_to_hass(hass)

    hass.config_entries.async_update_entry(
        mock_config_entry,
        options={**mock_config_entry.options, CONF_OPEN_TIMEOUT: 0.01},
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"binary_sensor.test_thermostat_contact_sensors_thermostat_paused"

    # Open a sensor
    hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door Contact"})
    await hass.async_block_till_done()

    # Wait for timeout
    await asyncio.sleep(1)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.attributes.get("triggered_by") == "Front Door Contact"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_device_info(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test binary sensor is associated with correct device."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    devices = device_registry.devices

    # Find our device
    our_device = None
    for device in devices.values():
        if (DOMAIN, mock_config_entry.entry_id) in device.identifiers:
            our_device = device
            break

    assert our_device is not None
    assert our_device.name == "Test Thermostat Contact Sensors"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_binary_sensor_restores_state(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test binary sensor restores paused state from previous run."""
    from homeassistant.const import STATE_ON
    from homeassistant.core import State
    from unittest.mock import patch

    mock_config_entry.add_to_hass(hass)

    entity_id = "binary_sensor.test_thermostat_contact_sensors_thermostat_paused"

    # Create a mock last state that was paused
    mock_last_state = State(
        entity_id,
        STATE_ON,
        {
            "thermostat": "climate.test_thermostat",
            "previous_mode": "cool",
            "open_count": 1,
            "triggered_by": "Front Door",
        },
    )

    # Patch RestoreEntity.async_get_last_state to return our mock state
    with patch(
        "homeassistant.helpers.restore_state.RestoreEntity.async_get_last_state",
        return_value=mock_last_state,
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # Check coordinator state was restored
    coordinator = mock_config_entry.runtime_data
    assert coordinator.is_paused is True
    assert coordinator.previous_hvac_mode == "cool"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
