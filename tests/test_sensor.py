"""Tests for the sensor platform."""
from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from homeassistant.components.climate import HVACMode
from homeassistant.util.unit_conversion import TemperatureConverter

from custom_components.thermostat_contact_sensors.const import DOMAIN
from custom_components.thermostat_contact_sensors.thermostat_control import ThermostatState

from .conftest import (
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
    TEST_AREA_LIVING_ROOM,
    TEST_AREA_BEDROOM,
    TEST_THERMOSTAT,
    TEST_TEMP_SENSOR_1,
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


def _entity_id_for_unique_id(hass: HomeAssistant, unique_id: str) -> str:
    entity_registry = er.async_get(hass)
    for entry in entity_registry.entities.values():
        if entry.unique_id == unique_id:
            return entry.entity_id
    raise AssertionError(f"Entity with unique_id={unique_id} not found")


async def test_room_temperature_sensors_created_for_all_enabled_areas(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Room temperature sensors should exist for each enabled area.

    The Bedroom in our default test config has no temperature sensors configured,
    so its room temperature sensor should exist but show unknown.
    """
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )
    bedroom_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_BEDROOM}_temperature"
    )

    living_state = hass.states.get(living_entity_id)
    bedroom_state = hass.states.get(bedroom_entity_id)

    assert living_state is not None
    assert bedroom_state is not None

    # Bedroom has no configured temperature sensors in the default test config.
    assert bedroom_state.state == "unknown"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_room_temperature_sensor_overall_temp_uses_trend_min_max(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Overall room temperature should be best-case by trend (min/max).

    - Trend=HEAT => warmest sensor
    - Trend=COOL => coolest sensor
    """
    # Seed one temperature sensor that already exists in the default config.
    hass.states.async_set(
        TEST_TEMP_SENSOR_1,
        "70.0",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    hass.states.async_set(
        "sensor.living_room_temperature_2",
        "75.0",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    await hass.async_block_till_done()

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data

    # Add a second temp sensor to the living room so we can test min/max.
    coordinator.areas_config[TEST_AREA_LIVING_ROOM]["temperature_sensors"] = [
        TEST_TEMP_SENSOR_1,
        "sensor.living_room_temperature_2",
    ]

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )

    expected_heat = TemperatureConverter.convert(
        75.0,
        UnitOfTemperature.FAHRENHEIT,
        hass.config.units.temperature_unit,
    )
    expected_cool = TemperatureConverter.convert(
        70.0,
        UnitOfTemperature.FAHRENHEIT,
        hass.config.units.temperature_unit,
    )

    # Trend heat => pick warmest (75)
    coordinator._last_thermostat_state = ThermostatState(
        thermostat_entity_id=TEST_THERMOSTAT,
        hvac_mode=HVACMode.OFF,
        inferred_hvac_mode=HVACMode.HEAT,
    )
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()
    assert float(hass.states.get(living_entity_id).state) == pytest.approx(expected_heat, abs=0.1)

    # Trend cool => pick coolest (70)
    coordinator._last_thermostat_state = ThermostatState(
        thermostat_entity_id=TEST_THERMOSTAT,
        hvac_mode=HVACMode.OFF,
        inferred_hvac_mode=HVACMode.COOL,
    )
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()
    assert float(hass.states.get(living_entity_id).state) == pytest.approx(expected_cool, abs=0.1)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_room_temperature_sensor_uses_determining_temperature_from_room_state(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Temperature sensor should use determining_temperature from room_state when available.
    
    This ensures the sensor displays the same value that the thermostat controller
    is actually using, not an independent calculation.
    """
    from custom_components.thermostat_contact_sensors.thermostat_control import (
        RoomTemperatureState,
        SatiationReason,
    )

    # Set up temperature sensors with different values
    hass.states.async_set(
        TEST_TEMP_SENSOR_1,
        "70.0",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    hass.states.async_set(
        "sensor.living_room_temperature_2",
        "75.0",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    await hass.async_block_till_done()

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    
    # Add both sensors to config
    coordinator.areas_config[TEST_AREA_LIVING_ROOM]["temperature_sensors"] = [
        TEST_TEMP_SENSOR_1,
        "sensor.living_room_temperature_2",
    ]

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )

    # Create a room state with a specific determining_temperature
    # This simulates what the thermostat controller would set
    room_state = RoomTemperatureState(
        area_id=TEST_AREA_LIVING_ROOM,
        area_name="Living Room",
        temperature_sensors=[TEST_TEMP_SENSOR_1, "sensor.living_room_temperature_2"],
        sensor_readings={
            TEST_TEMP_SENSOR_1: 70.0,
            "sensor.living_room_temperature_2": 75.0,
        },
        is_satiated=False,
        satiation_reason=SatiationReason.NOT_SATIATED,
        is_active=True,
        determining_sensor=TEST_TEMP_SENSOR_1,
        determining_temperature=70.456,  # Specific value that should be displayed
        target_temperature=72.0,
    )

    # Set up thermostat state with the room state
    thermostat_state = ThermostatState(
        thermostat_entity_id=TEST_THERMOSTAT,
        hvac_mode=HVACMode.HEAT,
        target_temperature=72.0,
        room_states={TEST_AREA_LIVING_ROOM: room_state},
    )
    coordinator._last_thermostat_state = thermostat_state
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    # The sensor should display the determining_temperature from room_state
    # rounded to 1 decimal place (70.456 -> 70.5), but converted to HA's unit system
    expected = TemperatureConverter.convert(
        70.5,
        UnitOfTemperature.FAHRENHEIT,
        hass.config.units.temperature_unit,
    )
    state = hass.states.get(living_entity_id)
    assert float(state.state) == pytest.approx(expected, abs=0.1)
    assert state.attributes["determining_temperature"] == 70.456

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_room_temperature_sensor_falls_back_to_live_when_no_room_state(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Temperature sensor should compute from live sensors when room_state is None."""
    # Set up temperature sensors
    hass.states.async_set(
        TEST_TEMP_SENSOR_1,
        "72.3",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    await hass.async_block_till_done()

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )

    # Set thermostat state without any room states
    thermostat_state = ThermostatState(
        thermostat_entity_id=TEST_THERMOSTAT,
        hvac_mode=HVACMode.HEAT,
        target_temperature=72.0,
        room_states={},  # No room state for living room
    )
    coordinator._last_thermostat_state = thermostat_state
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    # Sensor should fall back to computing from live sensor states
    expected = TemperatureConverter.convert(
        72.3,
        UnitOfTemperature.FAHRENHEIT,
        hass.config.units.temperature_unit,
    )
    state = hass.states.get(living_entity_id)
    assert float(state.state) == pytest.approx(expected, abs=0.1)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_room_temperature_sensor_falls_back_when_determining_temp_is_none(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Temperature sensor should fall back to live computation when determining_temperature is None."""
    from custom_components.thermostat_contact_sensors.thermostat_control import (
        RoomTemperatureState,
        SatiationReason,
    )

    # Set up temperature sensor
    hass.states.async_set(
        TEST_TEMP_SENSOR_1,
        "68.7",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    await hass.async_block_till_done()

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )

    # Create room state with determining_temperature = None
    # This could happen if the room has no valid temperature readings in the controller
    room_state = RoomTemperatureState(
        area_id=TEST_AREA_LIVING_ROOM,
        area_name="Living Room",
        temperature_sensors=[TEST_TEMP_SENSOR_1],
        sensor_readings={},  # No readings in the room state
        is_satiated=False,
        satiation_reason=SatiationReason.NO_TEMP_SENSORS,
        is_active=False,
        determining_sensor=None,
        determining_temperature=None,  # Explicitly None
        target_temperature=None,
    )

    thermostat_state = ThermostatState(
        thermostat_entity_id=TEST_THERMOSTAT,
        hvac_mode=HVACMode.HEAT,
        target_temperature=72.0,
        room_states={TEST_AREA_LIVING_ROOM: room_state},
    )
    coordinator._last_thermostat_state = thermostat_state
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    # Should fall back to live sensor reading
    expected = TemperatureConverter.convert(
        68.7,
        UnitOfTemperature.FAHRENHEIT,
        hass.config.units.temperature_unit,
    )
    state = hass.states.get(living_entity_id)
    assert float(state.state) == pytest.approx(expected, abs=0.1)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)


async def test_room_temperature_sensor_rounding(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Temperature sensor should round to 1 decimal place."""
    from custom_components.thermostat_contact_sensors.thermostat_control import (
        RoomTemperatureState,
        SatiationReason,
    )

    hass.states.async_set(
        TEST_TEMP_SENSOR_1,
        "73.0",
        {"unit_of_measurement": "°F", "device_class": "temperature"},
    )
    await hass.async_block_till_done()

    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data

    living_entity_id = _entity_id_for_unique_id(
        hass, f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_temperature"
    )

    # Test various rounding scenarios (in Fahrenheit, but converted to HA's unit system)
    test_cases = [
        (71.924, 71.9),  # Round down
        (71.95, 72.0),   # Round up
        (71.949, 71.9),  # Round down
        (71.0, 71.0),    # Exact decimal
        (71.05, 71.1),   # Round up from .05
    ]

    for determining_temp, expected_rounded in test_cases:
        room_state = RoomTemperatureState(
            area_id=TEST_AREA_LIVING_ROOM,
            area_name="Living Room",
            temperature_sensors=[TEST_TEMP_SENSOR_1],
            sensor_readings={TEST_TEMP_SENSOR_1: 73.0},
            determining_sensor=TEST_TEMP_SENSOR_1,
            determining_temperature=determining_temp,
            is_satiated=False,
            satiation_reason=SatiationReason.NOT_SATIATED,
        )

        thermostat_state = ThermostatState(
            thermostat_entity_id=TEST_THERMOSTAT,
            hvac_mode=HVACMode.HEAT,
            room_states={TEST_AREA_LIVING_ROOM: room_state},
        )
        coordinator._last_thermostat_state = thermostat_state
        coordinator.async_set_updated_data(None)
        await hass.async_block_till_done()

        # Convert expected rounded value to HA's unit system
        expected = TemperatureConverter.convert(
            expected_rounded,
            UnitOfTemperature.FAHRENHEIT,
            hass.config.units.temperature_unit,
        )
        state = hass.states.get(living_entity_id)
        assert float(state.state) == pytest.approx(expected, abs=0.1), f"For {determining_temp}°F, expected {expected_rounded}°F (converted to {expected}) but got {state.state}"

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
