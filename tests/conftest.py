"""Fixtures for Thermostat Contact Sensors tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
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


# Test entity IDs
TEST_THERMOSTAT = "climate.test_thermostat"
TEST_SENSOR_1 = "binary_sensor.front_door_contact"
TEST_SENSOR_2 = "binary_sensor.back_window_contact"
TEST_SENSOR_3 = "binary_sensor.garage_door_contact"
TEST_MOTION_SENSOR_1 = "binary_sensor.living_room_motion"  # Motion sensor for occupancy
TEST_MOTION_SENSOR_2 = "binary_sensor.bedroom_motion"  # Motion sensor for occupancy
TEST_TEMP_SENSOR_1 = "sensor.living_room_temperature"
TEST_OTHER_SENSOR_1 = "sensor.living_room_humidity"
TEST_NOTIFY_SERVICE = "notify.test_notify"

# Test area IDs
TEST_AREA_LIVING_ROOM = "living_room"
TEST_AREA_BEDROOM = "bedroom"


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Enable custom integrations for all tests."""
    pass


def get_test_areas_config() -> dict[str, dict]:
    """Get test areas configuration."""
    return {
        TEST_AREA_LIVING_ROOM: {
            CONF_AREA_ID: TEST_AREA_LIVING_ROOM,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1, TEST_SENSOR_2],  # Door/window sensors for pause
            CONF_BINARY_SENSORS: [TEST_MOTION_SENSOR_1],  # Motion/occupancy sensors
            CONF_TEMPERATURE_SENSORS: [TEST_TEMP_SENSOR_1],
            CONF_SENSORS: [TEST_OTHER_SENSOR_1],
        },
        TEST_AREA_BEDROOM: {
            CONF_AREA_ID: TEST_AREA_BEDROOM,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [TEST_SENSOR_3],  # Door/window sensors for pause
            CONF_BINARY_SENSORS: [TEST_MOTION_SENSOR_2],  # Motion/occupancy sensors
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
        },
    }


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Thermostat Contact Sensors",
        version=3,  # Set to version 3 to skip migration (uses per-area contact_sensors)
        data={
            "name": "Test Thermostat Contact Sensors",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_AREAS: get_test_areas_config(),
        },
        options={
            CONF_MIN_OCCUPANCY_MINUTES: DEFAULT_MIN_OCCUPANCY_MINUTES,
            CONF_TEMPERATURE_DEADBAND: DEFAULT_TEMPERATURE_DEADBAND,
            CONF_MIN_CYCLE_ON_MINUTES: DEFAULT_MIN_CYCLE_ON_MINUTES,
            CONF_MIN_CYCLE_OFF_MINUTES: DEFAULT_MIN_CYCLE_OFF_MINUTES,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
        entry_id="test_entry_id",
        unique_id=TEST_THERMOSTAT,
    )


@pytest.fixture
def mock_config_entry_no_notify() -> MockConfigEntry:
    """Create a mock config entry without notifications."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Thermostat No Notify",
        version=3,  # Set to version 3 to skip migration
        data={
            "name": "Test Thermostat No Notify",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_AREAS: get_test_areas_config(),
        },
        options={
            CONF_MIN_OCCUPANCY_MINUTES: DEFAULT_MIN_OCCUPANCY_MINUTES,
            CONF_TEMPERATURE_DEADBAND: DEFAULT_TEMPERATURE_DEADBAND,
            CONF_MIN_CYCLE_ON_MINUTES: DEFAULT_MIN_CYCLE_ON_MINUTES,
            CONF_MIN_CYCLE_OFF_MINUTES: DEFAULT_MIN_CYCLE_OFF_MINUTES,
            CONF_OPEN_TIMEOUT: 2,
            CONF_CLOSE_TIMEOUT: 2,
            CONF_NOTIFY_SERVICE: "",
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
        entry_id="test_entry_no_notify",
        unique_id=f"{TEST_THERMOSTAT}_no_notify",
    )


@pytest.fixture
async def setup_test_entities(hass: HomeAssistant) -> None:
    """Set up test entities."""
    # Set up thermostat with fan mode support
    hass.states.async_set(
        TEST_THERMOSTAT,
        HVACMode.HEAT,
        {
            "friendly_name": "Test Thermostat",
            "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
            "current_temperature": 20,
            "temperature": 22,
            "fan_mode": "on",
            "fan_modes": ["on", "auto"],
            "supported_features": ClimateEntityFeature.FAN_MODE,
        },
    )

    # Set up contact sensors (all closed initially)
    hass.states.async_set(
        TEST_SENSOR_1,
        STATE_OFF,
        {"friendly_name": "Front Door Contact", "device_class": "door"},
    )
    hass.states.async_set(
        TEST_SENSOR_2,
        STATE_OFF,
        {"friendly_name": "Back Window Contact", "device_class": "window"},
    )
    hass.states.async_set(
        TEST_SENSOR_3,
        STATE_OFF,
        {"friendly_name": "Garage Door Contact", "device_class": "garage_door"},
    )

    # Set up motion sensors (all off initially)
    hass.states.async_set(
        TEST_MOTION_SENSOR_1,
        STATE_OFF,
        {"friendly_name": "Living Room Motion", "device_class": "motion"},
    )
    hass.states.async_set(
        TEST_MOTION_SENSOR_2,
        STATE_OFF,
        {"friendly_name": "Bedroom Motion", "device_class": "motion"},
    )

    await hass.async_block_till_done()


@pytest.fixture
def mock_notify_service(hass: HomeAssistant) -> AsyncMock:
    """Mock the notify service."""
    mock_service = AsyncMock()
    hass.services.async_register(
        "notify",
        "test_notify",
        mock_service,
    )
    return mock_service


@pytest.fixture
def mock_climate_service(hass: HomeAssistant) -> AsyncMock:
    """Mock the climate set_hvac_mode and set_fan_mode services."""
    mock_hvac_service = AsyncMock()
    mock_fan_service = AsyncMock()

    async def handle_set_hvac_mode(call):
        """Handle the set_hvac_mode service call."""
        entity_id = call.data.get("entity_id")
        hvac_mode = call.data.get("hvac_mode")
        # Update the state
        current_attrs = hass.states.get(entity_id).attributes if hass.states.get(entity_id) else {}
        hass.states.async_set(entity_id, hvac_mode, current_attrs)
        await mock_hvac_service(call)

    async def handle_set_fan_mode(call):
        """Handle the set_fan_mode service call."""
        entity_id = call.data.get("entity_id")
        fan_mode = call.data.get("fan_mode")
        # Update the fan_mode attribute
        state = hass.states.get(entity_id)
        if state:
            current_attrs = dict(state.attributes)
            current_attrs["fan_mode"] = fan_mode
            hass.states.async_set(entity_id, state.state, current_attrs)
        await mock_fan_service(call)

    hass.services.async_register(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        handle_set_hvac_mode,
    )
    hass.services.async_register(
        CLIMATE_DOMAIN,
        "set_fan_mode",
        handle_set_fan_mode,
    )
    
    # Return the hvac mock for backward compatibility, but attach fan mock as attribute
    mock_hvac_service.fan_mode_mock = mock_fan_service
    return mock_hvac_service


@pytest.fixture
def mock_fan_mode_service(hass: HomeAssistant, mock_climate_service: AsyncMock) -> AsyncMock:
    """Get the fan mode service mock (registered by mock_climate_service)."""
    return mock_climate_service.fan_mode_mock


@pytest.fixture
async def setup_area_registry(hass: HomeAssistant) -> None:
    """Set up area registry with test areas.
    
    Creates areas with names that will generate IDs matching TEST_AREA_* constants.
    """
    area_reg = ar.async_get(hass)

    # Create test areas - the IDs are auto-generated as slugified names
    # "Living Room" -> "living_room", "Bedroom" -> "bedroom"
    area_reg.async_create(name="Living Room")
    area_reg.async_create(name="Bedroom")


@pytest.fixture
async def setup_entity_registry(hass: HomeAssistant, setup_area_registry) -> None:
    """Set up entity registry with test entities assigned to areas."""
    entity_reg = er.async_get(hass)

    # Register binary sensors and get the actual entity entries
    entry = entity_reg.async_get_or_create(
        "binary_sensor",
        "test",
        "front_door_contact",
        suggested_object_id="front_door_contact",
        original_device_class="door",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_LIVING_ROOM)

    entry = entity_reg.async_get_or_create(
        "binary_sensor",
        "test",
        "back_window_contact",
        suggested_object_id="back_window_contact",
        original_device_class="window",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_LIVING_ROOM)

    entry = entity_reg.async_get_or_create(
        "binary_sensor",
        "test",
        "garage_door_contact",
        suggested_object_id="garage_door_contact",
        original_device_class="garage_door",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_BEDROOM)

    # Register motion sensors for occupancy detection
    entry = entity_reg.async_get_or_create(
        "binary_sensor",
        "test",
        "living_room_motion",
        suggested_object_id="living_room_motion",
        original_device_class="motion",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_LIVING_ROOM)

    entry = entity_reg.async_get_or_create(
        "binary_sensor",
        "test",
        "bedroom_motion",
        suggested_object_id="bedroom_motion",
        original_device_class="motion",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_BEDROOM)

    # Register temperature sensor
    entry = entity_reg.async_get_or_create(
        "sensor",
        "test",
        "living_room_temperature",
        suggested_object_id="living_room_temperature",
        original_device_class="temperature",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_LIVING_ROOM)

    # Register other sensor
    entry = entity_reg.async_get_or_create(
        "sensor",
        "test",
        "living_room_humidity",
        suggested_object_id="living_room_humidity",
        original_device_class="humidity",
    )
    entity_reg.async_update_entity(entry.entity_id, area_id=TEST_AREA_LIVING_ROOM)


def get_test_config_data() -> dict[str, Any]:
    """Get test configuration data."""
    return {
        "name": "Test Thermostat Contact Sensors",
        CONF_THERMOSTAT: TEST_THERMOSTAT,
        CONF_AREAS: get_test_areas_config(),
    }


def get_test_config_options() -> dict[str, Any]:
    """Get test configuration options."""
    return {
        CONF_MIN_OCCUPANCY_MINUTES: DEFAULT_MIN_OCCUPANCY_MINUTES,
        CONF_TEMPERATURE_DEADBAND: DEFAULT_TEMPERATURE_DEADBAND,
        CONF_MIN_CYCLE_ON_MINUTES: DEFAULT_MIN_CYCLE_ON_MINUTES,
        CONF_MIN_CYCLE_OFF_MINUTES: DEFAULT_MIN_CYCLE_OFF_MINUTES,
        CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
        CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
        CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
        CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
        CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
        CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
        CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
        CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
    }
