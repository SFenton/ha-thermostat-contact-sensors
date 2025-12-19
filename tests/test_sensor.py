"""Tests for the sensor platform."""
from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.thermostat_contact_sensors.const import DOMAIN

from .conftest import (
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_sensor_creation(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor entity is created."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == "0"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_unique_id(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor has correct unique ID."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"
    entry = entity_registry.async_get(entity_id)

    assert entry is not None
    assert entry.unique_id == f"{mock_config_entry.entry_id}_open_count"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_count_updates(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor count updates when sensors open/close."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"

    # Initially 0
    state = hass.states.get(entity_id)
    assert state.state == "0"

    # Open first sensor
    hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "1"

    # Open second sensor
    hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "2"

    # Close first sensor
    hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "1"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_attributes_open_sensors(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor attributes include open sensor list."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"

    # Open sensors
    hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
    hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)

    assert TEST_SENSOR_1 in state.attributes.get("open_sensors", [])
    assert TEST_SENSOR_2 in state.attributes.get("open_sensors", [])
    assert "Front Door" in state.attributes.get("open_sensor_names", [])
    assert "Back Window" in state.attributes.get("open_sensor_names", [])

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_attributes_door_window_counts(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor attributes include door and window counts."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"

    # Open door and window
    hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
    hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)

    assert state.attributes.get("open_doors") == 1
    assert state.attributes.get("open_windows") == 1

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_attributes_monitored_sensors(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor attributes include list of monitored sensors."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"
    state = hass.states.get(entity_id)

    monitored = state.attributes.get("monitored_sensors", [])
    assert TEST_SENSOR_1 in monitored
    assert TEST_SENSOR_2 in monitored
    assert TEST_SENSOR_3 in monitored
    assert state.attributes.get("total_monitored") == 3

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_unit_of_measurement(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor has correct unit of measurement."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"
    state = hass.states.get(entity_id)

    assert state.attributes.get("unit_of_measurement") == "sensors"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_sensor_device_info(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test sensor is associated with correct device."""
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

    # Verify sensor is linked to device
    entity_registry = er.async_get(hass)
    entity_id = f"sensor.test_thermostat_contact_sensors_open_sensors"
    entry = entity_registry.async_get(entity_id)

    assert entry is not None
    assert entry.device_id == our_device.id

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
