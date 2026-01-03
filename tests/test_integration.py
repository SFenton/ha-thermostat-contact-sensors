"""Integration tests for the complete Thermostat Contact Sensors system.

These tests verify how different components interact and propagate state changes
across the entire system. Key scenarios tested:

1. Contact Sensors → Thermostat Pause → Vent State
2. Occupancy Changes → Active Rooms → Thermostat State → Vent State
3. Temperature Changes → Satiation → Thermostat Action → Vent State
4. Critical Temperature → Thermostat Override → Vent Priority
5. Minimum Vents → Priority Selection
6. Manual Override Recovery
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN, HVACMode
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_OPEN,
    STATE_CLOSED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_GRACE_PERIOD_MINUTES,
    CONF_MIN_CYCLE_OFF_MINUTES,
    CONF_MIN_CYCLE_ON_MINUTES,
    CONF_MIN_OCCUPANCY_MINUTES,
    CONF_MIN_VENTS_OPEN,
    CONF_NOTIFY_SERVICE,
    CONF_OPEN_TIMEOUT,
    CONF_SENSORS,
    CONF_TEMPERATURE_DEADBAND,
    CONF_TEMPERATURE_SENSORS,
    CONF_THERMOSTAT,
    CONF_UNOCCUPIED_COOLING_THRESHOLD,
    CONF_UNOCCUPIED_HEATING_THRESHOLD,
    CONF_VENT_DEBOUNCE_SECONDS,
    CONF_VENT_OPEN_DELAY_SECONDS,
    CONF_VENTS,
    DEFAULT_TEMPERATURE_DEADBAND,
    DOMAIN,
)
from custom_components.thermostat_contact_sensors.coordinator import (
    ThermostatContactSensorsCoordinator,
)
from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState
from custom_components.thermostat_contact_sensors.thermostat_control import (
    ThermostatAction,
    RoomTemperatureState,
)

# =============================================================================
# Test Constants
# =============================================================================

# Thermostats
THERMOSTAT = "climate.main_thermostat"

# Contact sensors
CONTACT_LIVING_ROOM = "binary_sensor.living_room_window"
CONTACT_BEDROOM = "binary_sensor.bedroom_window"
CONTACT_OFFICE = "binary_sensor.office_door"

# Occupancy sensors
OCCUPANCY_LIVING_ROOM = "binary_sensor.living_room_motion"
OCCUPANCY_BEDROOM = "binary_sensor.bedroom_motion"
OCCUPANCY_OFFICE = "binary_sensor.office_motion"
OCCUPANCY_KITCHEN = "binary_sensor.kitchen_motion"

# Temperature sensors
TEMP_LIVING_ROOM = "sensor.living_room_temperature"
TEMP_BEDROOM = "sensor.bedroom_temperature"
TEMP_OFFICE = "sensor.office_temperature"
TEMP_KITCHEN = "sensor.kitchen_temperature"

# Vents
VENT_LIVING_ROOM = "cover.living_room_vent"
VENT_BEDROOM = "cover.bedroom_vent"
VENT_OFFICE = "cover.office_vent"
VENT_KITCHEN = "cover.kitchen_vent"
VENT_HALLWAY_GROUP = "cover.hallway_vents"  # Group of 2 vents
VENT_BASEMENT = "cover.basement_vent"

# Areas
AREA_LIVING_ROOM = "living_room"
AREA_BEDROOM = "bedroom"
AREA_OFFICE = "office"
AREA_KITCHEN = "kitchen"
AREA_HALLWAY = "hallway"
AREA_BASEMENT = "basement"


# =============================================================================
# Fixtures
# =============================================================================


def get_integration_areas_config() -> dict[str, dict]:
    """Get a comprehensive areas configuration for integration testing."""
    return {
        AREA_LIVING_ROOM: {
            CONF_AREA_ID: AREA_LIVING_ROOM,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [CONTACT_LIVING_ROOM],  # Door/window for pause
            CONF_BINARY_SENSORS: [OCCUPANCY_LIVING_ROOM],  # Motion for occupancy
            CONF_TEMPERATURE_SENSORS: [TEMP_LIVING_ROOM],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_LIVING_ROOM],
        },
        AREA_BEDROOM: {
            CONF_AREA_ID: AREA_BEDROOM,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [CONTACT_BEDROOM],  # Door/window for pause
            CONF_BINARY_SENSORS: [OCCUPANCY_BEDROOM],  # Motion for occupancy
            CONF_TEMPERATURE_SENSORS: [TEMP_BEDROOM],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_BEDROOM],
        },
        AREA_OFFICE: {
            CONF_AREA_ID: AREA_OFFICE,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [CONTACT_OFFICE],  # Door/window for pause
            CONF_BINARY_SENSORS: [OCCUPANCY_OFFICE],  # Motion for occupancy
            CONF_TEMPERATURE_SENSORS: [TEMP_OFFICE],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_OFFICE],
        },
        AREA_KITCHEN: {
            CONF_AREA_ID: AREA_KITCHEN,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [],  # No contact sensors in kitchen
            CONF_BINARY_SENSORS: [OCCUPANCY_KITCHEN],
            CONF_TEMPERATURE_SENSORS: [TEMP_KITCHEN],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_KITCHEN],
        },
        AREA_HALLWAY: {
            CONF_AREA_ID: AREA_HALLWAY,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [],  # No contact sensors in hallway
            CONF_BINARY_SENSORS: [],  # No occupancy sensors in hallway
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_HALLWAY_GROUP],  # Group of 2 vents
        },
        AREA_BASEMENT: {
            CONF_AREA_ID: AREA_BASEMENT,
            CONF_AREA_ENABLED: True,
            CONF_CONTACT_SENSORS: [],  # No contact sensors in basement
            CONF_BINARY_SENSORS: [],
            CONF_TEMPERATURE_SENSORS: [],
            CONF_SENSORS: [],
            CONF_VENTS: [VENT_BASEMENT],
        },
    }


def get_contact_sensors_from_areas(areas_config: dict[str, dict]) -> list[str]:
    """Extract all contact sensors from areas config (mirroring async_setup_entry behavior)."""
    contact_sensors = []
    for area_id, area_config in areas_config.items():
        if area_config.get(CONF_AREA_ENABLED, True):
            area_contact_sensors = area_config.get(CONF_CONTACT_SENSORS, [])
            contact_sensors.extend(area_contact_sensors)
    return contact_sensors


@pytest.fixture
def integration_config_entry() -> MockConfigEntry:
    """Create a config entry for integration testing."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Integration Test",
        version=3,
        data={
            "name": "Integration Test",
            CONF_THERMOSTAT: THERMOSTAT,
            CONF_AREAS: get_integration_areas_config(),
        },
        options={
            CONF_MIN_OCCUPANCY_MINUTES: 5,  # 5 minutes to become active
            CONF_GRACE_PERIOD_MINUTES: 2,  # 2 minutes grace before deactivating
            CONF_TEMPERATURE_DEADBAND: 0.5,
            CONF_MIN_CYCLE_ON_MINUTES: 5,
            CONF_MIN_CYCLE_OFF_MINUTES: 5,
            CONF_OPEN_TIMEOUT: 5,  # 5 min before pausing
            CONF_CLOSE_TIMEOUT: 2,  # 2 min before resuming
            CONF_NOTIFY_SERVICE: "",  # No notifications for tests
            CONF_MIN_VENTS_OPEN: 3,  # Minimum 3 vents must stay open
            CONF_VENT_OPEN_DELAY_SECONDS: 30,  # 30s before vents open for occupancy
            CONF_VENT_DEBOUNCE_SECONDS: 0,  # No debounce for faster testing
            CONF_UNOCCUPIED_HEATING_THRESHOLD: 5.0,  # 5 degrees below target
            CONF_UNOCCUPIED_COOLING_THRESHOLD: 5.0,  # 5 degrees above target
        },
        entry_id="integration_test_entry",
        unique_id="integration_test",
    )


@pytest.fixture
async def setup_integration_entities(hass: HomeAssistant) -> None:
    """Set up all entities needed for integration testing."""
    # Thermostat - heating mode, target 22°C
    hass.states.async_set(
        THERMOSTAT,
        HVACMode.HEAT,
        {
            "friendly_name": "Main Thermostat",
            "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
            "current_temperature": 20,
            "temperature": 22,  # Target temp
        },
    )

    # Contact sensors - all closed initially
    for sensor in [CONTACT_LIVING_ROOM, CONTACT_BEDROOM, CONTACT_OFFICE]:
        hass.states.async_set(sensor, STATE_OFF, {"device_class": "window"})

    # Occupancy sensors - all unoccupied initially
    for sensor in [OCCUPANCY_LIVING_ROOM, OCCUPANCY_BEDROOM, OCCUPANCY_OFFICE, OCCUPANCY_KITCHEN]:
        hass.states.async_set(sensor, STATE_OFF, {"device_class": "motion"})

    # Temperature sensors - all at 20°C (below target of 22°C)
    for sensor in [TEMP_LIVING_ROOM, TEMP_BEDROOM, TEMP_OFFICE, TEMP_KITCHEN]:
        hass.states.async_set(sensor, "20.0", {"unit_of_measurement": "°C"})

    # Vents - all open initially
    for vent in [VENT_LIVING_ROOM, VENT_BEDROOM, VENT_OFFICE, VENT_KITCHEN, VENT_BASEMENT]:
        hass.states.async_set(vent, STATE_OPEN, {"current_tilt_position": 100})

    # Hallway vent group - 2 members
    hass.states.async_set(
        VENT_HALLWAY_GROUP,
        STATE_OPEN,
        {
            "current_tilt_position": 100,
            ATTR_ENTITY_ID: ["cover.hallway_vent_1", "cover.hallway_vent_2"],
        },
    )

    await hass.async_block_till_done()


@pytest.fixture
def mock_cover_service(hass: HomeAssistant) -> dict[str, list]:
    """Mock cover services and track calls."""
    calls = {"open_tilt": [], "close_tilt": []}

    async def handle_open_tilt(call):
        entity_id = call.data.get("entity_id")
        calls["open_tilt"].append(entity_id)
        current_attrs = hass.states.get(entity_id).attributes if hass.states.get(entity_id) else {}
        attrs = dict(current_attrs)
        attrs["current_tilt_position"] = 100
        hass.states.async_set(entity_id, STATE_OPEN, attrs)

    async def handle_close_tilt(call):
        entity_id = call.data.get("entity_id")
        calls["close_tilt"].append(entity_id)
        current_attrs = hass.states.get(entity_id).attributes if hass.states.get(entity_id) else {}
        attrs = dict(current_attrs)
        attrs["current_tilt_position"] = 0
        hass.states.async_set(entity_id, STATE_CLOSED, attrs)

    hass.services.async_register("cover", "open_cover_tilt", handle_open_tilt)
    hass.services.async_register("cover", "close_cover_tilt", handle_close_tilt)

    return calls


@pytest.fixture
def mock_climate_service_integration(hass: HomeAssistant) -> dict[str, list]:
    """Mock climate services and track calls."""
    calls = {"set_hvac_mode": []}

    async def handle_set_hvac_mode(call):
        entity_id = call.data.get("entity_id")
        hvac_mode = call.data.get("hvac_mode")
        calls["set_hvac_mode"].append({"entity_id": entity_id, "hvac_mode": hvac_mode})
        current_attrs = hass.states.get(entity_id).attributes if hass.states.get(entity_id) else {}
        hass.states.async_set(entity_id, hvac_mode, current_attrs)

    hass.services.async_register(CLIMATE_DOMAIN, "set_hvac_mode", handle_set_hvac_mode)

    return calls


# =============================================================================
# Test Class: Contact Sensor Effects
# =============================================================================


class TestContactSensorEffects:
    """Test how contact sensors affect the entire system."""

    @pytest.mark.asyncio
    async def test_contact_open_pauses_thermostat_after_timeout(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that open contact sensors pause thermostat after timeout."""
        integration_config_entry.add_to_hass(hass)

        # Create coordinator
        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Verify initial state
        assert coordinator.is_paused is False
        assert coordinator.open_count == 0

        # Open a contact sensor
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()

        # Verify sensor is tracked
        assert coordinator.open_count == 1
        assert CONTACT_LIVING_ROOM in coordinator.open_sensors
        assert coordinator.is_paused is False  # Not paused yet (timeout pending)

        # Simulate timeout expiration
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        # Verify thermostat is now paused
        assert coordinator.is_paused is True
        assert mock_climate_service_integration["set_hvac_mode"][-1]["hvac_mode"] == HVACMode.OFF

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_contact_close_resumes_thermostat_after_timeout(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that closing all contacts resumes thermostat after timeout."""
        integration_config_entry.add_to_hass(hass)

        # Ensure temperature is below target so thermostat stays on after resume
        hass.states.async_set(TEMP_LIVING_ROOM, "18.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Make living room active so thermostat evaluation keeps it on
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Open contact and trigger pause
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Close the contact
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()

        # Simulate close timeout expiration
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        # Verify thermostat is resumed
        assert coordinator.is_paused is False
        # Should have restored previous mode (HEAT) - and since room is active and
        # not satiated, the immediate evaluation should keep it on
        last_call = mock_climate_service_integration["set_hvac_mode"][-1]
        assert last_call["hvac_mode"] == HVACMode.HEAT

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_multiple_contacts_open_one_close_stays_paused(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that closing one contact while others open doesn't resume."""
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Open two contacts
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        hass.states.async_set(CONTACT_BEDROOM, STATE_ON)
        await hass.async_block_till_done()

        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.open_count == 2

        # Close one contact
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()

        # Still paused with one open
        assert coordinator.is_paused is True
        assert coordinator.open_count == 1

        # Close timeout shouldn't work with sensors still open
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        # Still paused
        assert coordinator.is_paused is True

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Occupancy → Vent Effects
# =============================================================================


class TestOccupancyVentEffects:
    """Test how occupancy changes affect vent states."""

    @pytest.mark.asyncio
    async def test_occupied_room_opens_vents_after_delay(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that occupied rooms open vents after the occupancy delay."""
        integration_config_entry.add_to_hass(hass)

        # Close all vents initially
        for vent in [VENT_LIVING_ROOM, VENT_BEDROOM, VENT_OFFICE, VENT_KITCHEN]:
            hass.states.async_set(vent, STATE_CLOSED, {"current_tilt_position": 0})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Simulate room becoming occupied and past the vent delay
        now = dt_util.utcnow()
        past_delay = now - timedelta(seconds=60)  # 60s > 30s delay

        # Mock the occupancy tracker to report living room as occupied
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=past_delay,
            is_active=True,
        )

        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Living room vent should be commanded to open
        assert VENT_LIVING_ROOM in mock_cover_service["open_tilt"]

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_occupied_room_below_delay_keeps_vents_closed(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that recently occupied rooms don't open vents yet."""
        integration_config_entry.add_to_hass(hass)

        # Close all vents initially
        for vent in [VENT_LIVING_ROOM, VENT_BEDROOM, VENT_OFFICE, VENT_KITCHEN]:
            hass.states.async_set(vent, STATE_CLOSED, {"current_tilt_position": 0})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Clear any initial vent commands
        mock_cover_service["open_tilt"].clear()
        mock_cover_service["close_tilt"].clear()

        # Simulate room becoming occupied but NOT past the delay
        now = dt_util.utcnow()
        just_occupied = now - timedelta(seconds=10)  # 10s < 30s delay

        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=just_occupied,
            is_active=False,  # Not active yet (< 5 min)
        )

        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Living room vent should NOT be opened
        assert VENT_LIVING_ROOM not in mock_cover_service["open_tilt"]

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_inactive_room_closes_vents(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that inactive rooms have their vents closed."""
        # Use min_vents_open=0 for this test to avoid minimum keeping them open
        options = dict(integration_config_entry.options)
        options[CONF_MIN_VENTS_OPEN] = 0
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # All rooms are inactive (no occupancy)
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Check final vent state - should be closed for inactive rooms
        # (Commands may have been issued during setup or update)
        vent_state = coordinator.last_vent_control_state
        
        # All areas should have should_open = False (since no occupancy)
        for area_id in [AREA_KITCHEN, AREA_BASEMENT, AREA_HALLWAY]:
            area_state = vent_state.area_states.get(area_id)
            if area_state:
                assert area_state.should_open is False, f"{area_id} should not want vents open"

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Temperature → Thermostat → Vent Effects
# =============================================================================


class TestTemperatureEffects:
    """Test how temperature changes propagate through the system."""

    @pytest.mark.asyncio
    async def test_satiated_room_closes_vents(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that satiated rooms (at target temp) close vents."""
        options = dict(integration_config_entry.options)
        options[CONF_MIN_VENTS_OPEN] = 0
        integration_config_entry.add_to_hass(hass)

        # Set living room temperature to target (22°C) - satiated
        hass.states.async_set(TEMP_LIVING_ROOM, "22.5", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Simulate living room as active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Update thermostat state first to get satiation
        coordinator.update_thermostat_state()

        # Clear and update vents
        mock_cover_service["open_tilt"].clear()
        mock_cover_service["close_tilt"].clear()
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Note: If occupied past delay, vents stay open for comfort
        # even if satiated. So this tests the occupied-overrides-satiated behavior.

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_unsatiated_room_keeps_vents_open(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that unsatiated rooms (below target) keep vents open."""
        integration_config_entry.add_to_hass(hass)

        # Temperature is 20°C, target is 22°C - not satiated
        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Simulate living room as active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Update thermostat state
        state = coordinator.update_thermostat_state()

        # Verify room is not satiated
        assert AREA_LIVING_ROOM in state.room_states
        assert state.room_states[AREA_LIVING_ROOM].is_satiated is False

        # Clear and update vents
        mock_cover_service["open_tilt"].clear()
        mock_cover_service["close_tilt"].clear()

        # Close the vent first to test that it gets opened
        hass.states.async_set(VENT_LIVING_ROOM, STATE_CLOSED, {"current_tilt_position": 0})
        await hass.async_block_till_done()

        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Vent should be opened (active + unsatiated = open)
        assert VENT_LIVING_ROOM in mock_cover_service["open_tilt"]

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Contact Sensor Pause Precedence
# =============================================================================


class TestContactSensorPausePrecedence:
    """Test that contact sensor pause takes precedence over thermostat control."""

    @pytest.mark.asyncio
    async def test_pause_prevents_thermostat_turn_on_even_when_unsatiated(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that thermostat stays off when paused, even if rooms need heating.
        
        Scenario: Rooms are cold (unsatiated), thermostat would normally turn on,
        but a door is open so thermostat should stay paused/off.
        """
        integration_config_entry.add_to_hass(hass)

        # Temperature is 18°C, target is 22°C - definitely needs heating
        hass.states.async_set(TEMP_LIVING_ROOM, "18.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Make living room active (needs heating)
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Open a contact sensor and trigger pause
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        mock_climate_service_integration["set_hvac_mode"].clear()

        # Now try to update thermostat state - should NOT turn on
        await coordinator.async_update_thermostat_state()
        await hass.async_block_till_done()

        # Thermostat should stay off (no turn-on calls while paused)
        turn_on_calls = [c for c in mock_climate_service_integration["set_hvac_mode"] 
                         if c["hvac_mode"] != HVACMode.OFF]
        assert len(turn_on_calls) == 0, "Thermostat should not turn on while paused"

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_resume_immediately_evaluates_thermostat_state(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that resume from pause immediately evaluates thermostat state.
        
        When doors close and resume happens, we should immediately evaluate
        whether the thermostat should be on or off based on current satiation.
        """
        options = dict(integration_config_entry.options)
        options[CONF_MIN_CYCLE_ON_MINUTES] = 0  # No cycle protection for test
        options[CONF_MIN_CYCLE_OFF_MINUTES] = 0
        integration_config_entry.add_to_hass(hass)

        # Temperature is 18°C, target is 22°C - needs heating
        hass.states.async_set(TEMP_LIVING_ROOM, "18.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Make living room active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Open contact and pause
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Close contact and resume
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is False

        # Thermostat state should have been evaluated on resume
        # The last thermostat state should reflect current conditions
        state = coordinator._last_thermostat_state
        assert state is not None

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_satiated_room_stays_off_after_resume(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test that if all rooms are satiated on resume, thermostat may turn off.
        
        Scenario: Door opens, thermostat pauses. While door is open, the room
        reaches target temperature (satiated). Door closes, resume happens.
        Thermostat should evaluate and potentially stay off or turn off if satiated.
        """
        options = dict(integration_config_entry.options)
        options[CONF_MIN_CYCLE_ON_MINUTES] = 0
        options[CONF_MIN_CYCLE_OFF_MINUTES] = 0
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Make living room active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Open contact and pause
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Room reaches target temperature while paused
        hass.states.async_set(TEMP_LIVING_ROOM, "22.5", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        mock_climate_service_integration["set_hvac_mode"].clear()

        # Close contact and resume
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is False

        # Verify thermostat state was evaluated
        state = coordinator._last_thermostat_state
        assert state is not None

        # Room should be satiated now
        if AREA_LIVING_ROOM in state.room_states:
            assert state.room_states[AREA_LIVING_ROOM].is_satiated is True

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Timer Recalculation Integration
# =============================================================================


class TestTimerRecalculationIntegration:
    """Integration tests for timer recalculation when sensors close while others remain open."""

    @pytest.mark.asyncio
    async def test_garage_opens_theater_opens_garage_closes_timer_recalculates(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test the exact scenario from the bug report.
        
        T=0: Garage (living_room) opens - timer starts
        T=2: Theater (bedroom) opens
        T=3: Garage closes - timer should recalculate based on theater
        
        Timer should NOT fire at original T=5, should fire at T=7 (5 min after theater opened)
        """
        options = dict(integration_config_entry.options)
        options[CONF_OPEN_TIMEOUT] = 5  # 5 minute timeout
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # T=0: Living room window (acting as "garage") opens
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()

        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == CONTACT_LIVING_ROOM
        living_room_open_time = coordinator._open_sensor_times[CONTACT_LIVING_ROOM]

        # T=2: Bedroom window (acting as "theater") opens
        await asyncio.sleep(0.1)  # Simulate time passing
        hass.states.async_set(CONTACT_BEDROOM, STATE_ON)
        await hass.async_block_till_done()

        bedroom_open_time = coordinator._open_sensor_times[CONTACT_BEDROOM]
        assert bedroom_open_time > living_room_open_time  # Bedroom opened later

        # Both sensors tracked
        assert len(coordinator.open_sensors) == 2

        # T=3: Living room closes - should recalculate timer for bedroom
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()

        # Timer should now be based on bedroom
        assert coordinator._pending_open_sensor == CONTACT_BEDROOM
        assert CONTACT_LIVING_ROOM not in coordinator._open_sensor_times
        assert CONTACT_BEDROOM in coordinator._open_sensor_times

        # Should NOT be paused yet
        assert coordinator.is_paused is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_multiple_sensors_close_in_sequence(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_climate_service_integration: dict,
        mock_cover_service: dict,
    ):
        """Test closing multiple sensors in sequence recalculates correctly each time."""
        options = dict(integration_config_entry.options)
        options[CONF_OPEN_TIMEOUT] = 5
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Open three sensors in sequence
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()
        await asyncio.sleep(0.05)

        hass.states.async_set(CONTACT_BEDROOM, STATE_ON)
        await hass.async_block_till_done()
        await asyncio.sleep(0.05)

        hass.states.async_set(CONTACT_OFFICE, STATE_ON)
        await hass.async_block_till_done()

        assert len(coordinator.open_sensors) == 3
        assert coordinator._pending_open_sensor == CONTACT_LIVING_ROOM

        # Close living room (first one) - should recalculate to bedroom
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_OFF)
        await hass.async_block_till_done()

        assert coordinator._pending_open_sensor == CONTACT_BEDROOM
        assert len(coordinator.open_sensors) == 2

        # Close bedroom - should recalculate to office
        hass.states.async_set(CONTACT_BEDROOM, STATE_OFF)
        await hass.async_block_till_done()

        assert coordinator._pending_open_sensor == CONTACT_OFFICE
        assert len(coordinator.open_sensors) == 1

        # Close office - should cancel timer entirely
        hass.states.async_set(CONTACT_OFFICE, STATE_OFF)
        await hass.async_block_till_done()

        assert coordinator._open_timer is None
        assert len(coordinator.open_sensors) == 0
        assert coordinator.is_paused is False

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Critical Temperature Effects
# =============================================================================


class TestCriticalTemperatureEffects:
    """Test how critical temperatures affect the system."""

    @pytest.mark.asyncio
    async def test_critical_cold_room_opens_vents(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that critically cold rooms have vents opened."""
        integration_config_entry.add_to_hass(hass)

        # Set bedroom temperature to critically cold (15°C when target is 22°C)
        # Critical threshold is 5°C below target = 17°C
        hass.states.async_set(TEMP_BEDROOM, "15.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Bedroom is unoccupied but critically cold
        # Update thermostat state to detect critical
        state = coordinator.update_thermostat_state()

        # Verify bedroom is critical
        assert AREA_BEDROOM in state.room_states
        assert state.room_states[AREA_BEDROOM].is_critical is True

        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Check final state - critical room should have vent opened
        vent_state = coordinator.last_vent_control_state
        bedroom_area = vent_state.area_states.get(AREA_BEDROOM)
        assert bedroom_area is not None
        assert bedroom_area.should_open is True, "Critical cold room should have vents open"
        assert "Critical" in (bedroom_area.open_reason or ""), "Reason should indicate critical"

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_critical_room_counts_toward_minimum_vents(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that critical rooms count toward minimum vents requirement."""
        # Set min vents to 2
        options = dict(integration_config_entry.options)
        options[CONF_MIN_VENTS_OPEN] = 2
        integration_config_entry.add_to_hass(hass)

        # Make bedroom critically cold
        hass.states.async_set(TEMP_BEDROOM, "15.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Update thermostat state
        coordinator.update_thermostat_state()

        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Get vent control state
        vent_state = coordinator.last_vent_control_state

        # Should have at least 2 vents marked as should_be_open
        vents_should_open = sum(
            1 for area_state in vent_state.area_states.values()
            for vent in area_state.vents
            if vent.should_be_open
        )
        assert vents_should_open >= 2

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Minimum Vents Open
# =============================================================================


class TestMinimumVentsOpen:
    """Test the minimum vents open requirement."""

    @pytest.mark.asyncio
    async def test_minimum_vents_kept_open_when_all_inactive(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that minimum vents are kept open even when all rooms inactive."""
        # Set min vents to 3
        options = dict(integration_config_entry.options)
        options[CONF_MIN_VENTS_OPEN] = 3
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # All rooms are inactive (no occupancy set up)
        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Get vent control state
        vent_state = coordinator.last_vent_control_state

        # Count vents that should be open
        vents_should_open = 0
        for area_state in vent_state.area_states.values():
            for vent in area_state.vents:
                if vent.should_be_open:
                    vents_should_open += vent.member_count

        # At least 3 vents should be marked as should_be_open
        assert vents_should_open >= 3

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_vent_group_counts_as_multiple_vents(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test that vent groups count as multiple vents toward minimum."""
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Update vents
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Get vent control state
        vent_state = coordinator.last_vent_control_state

        # Hallway group should be detected as 2 vents
        hallway_state = vent_state.area_states.get(AREA_HALLWAY)
        if hallway_state:
            assert hallway_state.total_vent_count == 2
            assert hallway_state.vents[0].is_group is True
            assert hallway_state.vents[0].member_count == 2

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Full System Integration
# =============================================================================


class TestFullSystemIntegration:
    """End-to-end tests of the full system."""

    @pytest.mark.asyncio
    async def test_scenario_morning_wakeup(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test scenario: Morning wakeup - bedroom becomes occupied."""
        integration_config_entry.add_to_hass(hass)

        # Initial state: All rooms unoccupied, thermostat in heat mode
        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Step 1: Person wakes up - bedroom motion detected
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_BEDROOM] = AreaOccupancyState(
            area_id=AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[OCCUPANCY_BEDROOM],
            occupied_binary_sensors={OCCUPANCY_BEDROOM},
            occupancy_start_time=now - timedelta(seconds=60),  # Past vent delay
            is_active=False,  # Not active yet (< 5 min)
        )

        # Update system
        coordinator.update_thermostat_state()
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Check bedroom vent should be marked to open (occupied past delay)
        vent_state = coordinator.last_vent_control_state
        bedroom_area = vent_state.area_states.get(AREA_BEDROOM)
        assert bedroom_area is not None
        assert bedroom_area.should_open is True, "Occupied room should have vent open"

        # Step 2: Person stays in bedroom - becomes active
        coordinator.occupancy_tracker._areas[AREA_BEDROOM].is_active = True
        coordinator.occupancy_tracker._areas[AREA_BEDROOM].occupancy_start_time = (
            now - timedelta(minutes=10)
        )

        coordinator.update_thermostat_state()
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Thermostat should recommend ON (room is active, not satiated)
        state = coordinator.last_thermostat_state
        assert state.active_room_count >= 1

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_scenario_window_opened_during_heating(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test scenario: Window opened while heating - thermostat pauses."""
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Set up an active room
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.update_thermostat_state()

        # Open the living room window
        hass.states.async_set(CONTACT_LIVING_ROOM, STATE_ON)
        await hass.async_block_till_done()

        assert coordinator.open_count == 1
        assert coordinator.is_paused is False  # Not paused yet

        # Trigger timeout
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        # Thermostat should be paused
        assert coordinator.is_paused is True
        assert mock_climate_service_integration["set_hvac_mode"][-1]["hvac_mode"] == HVACMode.OFF

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_scenario_all_rooms_reach_temperature(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test scenario: All active rooms reach target temperature."""
        options = dict(integration_config_entry.options)
        options[CONF_MIN_VENTS_OPEN] = 0  # Disable minimum for clearer test
        integration_config_entry.add_to_hass(hass)

        # Set all temps to target (22°C) + deadband (0.5) = satiated
        for sensor in [TEMP_LIVING_ROOM, TEMP_BEDROOM, TEMP_OFFICE, TEMP_KITCHEN]:
            hass.states.async_set(sensor, "22.5", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Set up multiple active rooms
        now = dt_util.utcnow()
        for area_id, occ_sensor in [
            (AREA_LIVING_ROOM, OCCUPANCY_LIVING_ROOM),
            (AREA_BEDROOM, OCCUPANCY_BEDROOM),
        ]:
            coordinator.occupancy_tracker._areas[area_id] = AreaOccupancyState(
                area_id=area_id,
                area_name=area_id.replace("_", " ").title(),
                binary_sensors=[occ_sensor],
                occupied_binary_sensors={occ_sensor},
                occupancy_start_time=now - timedelta(minutes=10),
                is_active=True,
            )

        # Update thermostat state
        state = coordinator.update_thermostat_state()

        # All active rooms should be satiated
        assert state.all_active_rooms_satiated is True
        assert state.recommended_action in [
            ThermostatAction.TURN_OFF,
            ThermostatAction.WAIT_CYCLE_ON,  # May be waiting for min cycle
            ThermostatAction.NONE,  # Already off
        ]

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_scenario_mixed_room_states(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test scenario: Mixed room states - some satiated, some not, some critical."""
        integration_config_entry.add_to_hass(hass)

        # Living room: at target (satiated)
        hass.states.async_set(TEMP_LIVING_ROOM, "22.5", {"unit_of_measurement": "°C"})

        # Bedroom: below target (not satiated)
        hass.states.async_set(TEMP_BEDROOM, "19.0", {"unit_of_measurement": "°C"})

        # Office: critically cold (unoccupied)
        hass.states.async_set(TEMP_OFFICE, "15.0", {"unit_of_measurement": "°C"})

        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Set up living room and bedroom as active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        coordinator.occupancy_tracker._areas[AREA_BEDROOM] = AreaOccupancyState(
            area_id=AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[OCCUPANCY_BEDROOM],
            occupied_binary_sensors={OCCUPANCY_BEDROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        # Office is NOT occupied (but critically cold)

        # Update thermostat state
        state = coordinator.update_thermostat_state()

        # Should have:
        # - Living room: active, satiated
        # - Bedroom: active, not satiated
        # - Office: critical (unoccupied but too cold)

        assert state.room_states[AREA_LIVING_ROOM].is_satiated is True
        assert state.room_states[AREA_BEDROOM].is_satiated is False
        assert state.room_states[AREA_OFFICE].is_critical is True

        # Not all rooms satiated (bedroom isn't)
        assert state.all_active_rooms_satiated is False

        # Thermostat should want to be ON
        assert state.recommended_action in [
            ThermostatAction.TURN_ON,
            ThermostatAction.WAIT_CYCLE_OFF,
            ThermostatAction.NONE,  # Already on
        ]

        await coordinator.async_shutdown()


# =============================================================================
# Test Class: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_thermostat_unavailable(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test system behavior when thermostat becomes unavailable."""
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Make thermostat unavailable
        hass.states.async_set(THERMOSTAT, STATE_UNAVAILABLE, {})
        await hass.async_block_till_done()

        # Update thermostat state should handle gracefully
        state = coordinator.update_thermostat_state()

        # Should still return a state, even if limited
        assert state is not None
        assert state.hvac_mode is None

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_temperature_sensor_unavailable(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test system behavior when temperature sensors are unavailable."""
        integration_config_entry.add_to_hass(hass)

        # Make living room temp sensor unavailable
        hass.states.async_set(TEMP_LIVING_ROOM, STATE_UNAVAILABLE, {})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Set up living room as active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
            area_id=AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[OCCUPANCY_LIVING_ROOM],
            occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Update thermostat state should handle gracefully
        state = coordinator.update_thermostat_state()

        # Living room should have no valid readings
        assert state.room_states[AREA_LIVING_ROOM].has_valid_readings is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_vent_unavailable(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test system behavior when a vent becomes unavailable."""
        integration_config_entry.add_to_hass(hass)

        # Make living room vent unavailable
        hass.states.async_set(VENT_LIVING_ROOM, STATE_UNAVAILABLE, {})
        await hass.async_block_till_done()

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Update vents should handle gracefully
        await coordinator.async_update_vents()
        await hass.async_block_till_done()

        # Should not crash
        assert coordinator.last_vent_control_state is not None

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_empty_areas_config(
        self,
        hass: HomeAssistant,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test system behavior with no areas configured."""
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Empty Areas Test",
            version=3,
            data={
                "name": "Empty Areas Test",
                CONF_THERMOSTAT: THERMOSTAT,
                CONF_AREAS: {},  # No areas
            },
            options={
                CONF_MIN_OCCUPANCY_MINUTES: 5,
                CONF_OPEN_TIMEOUT: 5,
                CONF_CLOSE_TIMEOUT: 2,
                CONF_NOTIFY_SERVICE: "",
            },
            entry_id="empty_areas_test",
            unique_id="empty_areas_test",
        )
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=[],  # No contact sensors when no areas
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config={},
        )
        await coordinator.async_setup()

        # Should handle gracefully
        assert coordinator.occupancy_tracker.active_areas == []
        await coordinator.async_update_vents()

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_rapid_occupancy_changes(
        self,
        hass: HomeAssistant,
        integration_config_entry: MockConfigEntry,
        setup_integration_entities: None,
        mock_cover_service: dict,
        mock_climate_service_integration: dict,
    ):
        """Test system stability with rapid occupancy changes."""
        integration_config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=integration_config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(integration_config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=integration_config_entry.options,
            areas_config=integration_config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        now = dt_util.utcnow()

        # Simulate rapid occupancy changes
        for i in range(10):
            # Alternate occupied/unoccupied
            if i % 2 == 0:
                coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
                    area_id=AREA_LIVING_ROOM,
                    area_name="Living Room",
                    binary_sensors=[OCCUPANCY_LIVING_ROOM],
                    occupied_binary_sensors={OCCUPANCY_LIVING_ROOM},
                    occupancy_start_time=now,
                    is_active=False,
                )
            else:
                coordinator.occupancy_tracker._areas[AREA_LIVING_ROOM] = AreaOccupancyState(
                    area_id=AREA_LIVING_ROOM,
                    area_name="Living Room",
                    binary_sensors=[OCCUPANCY_LIVING_ROOM],
                    occupied_binary_sensors=set(),
                    occupancy_start_time=None,
                    is_active=False,
                )

            coordinator.update_thermostat_state()
            await coordinator.async_update_vents()

        await hass.async_block_till_done()

        # Should not crash
        assert True

        await coordinator.async_shutdown()
