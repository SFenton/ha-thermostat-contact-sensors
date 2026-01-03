"""Tests for Room Occupancy and Thermostat Control sensors."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.thermostat_contact_sensors.const import DOMAIN
from custom_components.thermostat_contact_sensors.sensor import (
    RoomOccupancySensor,
    ThermostatControlSensor,
)

from .conftest import (
    TEST_AREA_BEDROOM,
    TEST_AREA_LIVING_ROOM,
    TEST_MOTION_SENSOR_1,
    TEST_MOTION_SENSOR_2,
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
    TEST_TEMP_SENSOR_1,
    TEST_THERMOSTAT,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


# =============================================================================
# Room Occupancy Sensor Tests
# =============================================================================


class TestRoomOccupancySensor:
    """Tests for the RoomOccupancySensor class."""

    async def test_sensor_creation_per_area(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that a sensor is created for each enabled area."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Should have sensors for living_room and bedroom
        living_room_entity = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"
        bedroom_entity = f"sensor.test_thermostat_contact_sensors_bedroom_occupancy"

        living_room_state = hass.states.get(living_room_entity)
        bedroom_state = hass.states.get(bedroom_entity)

        assert living_room_state is not None
        assert bedroom_state is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_sensor_unique_id(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test sensor has correct unique ID."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_registry = er.async_get(hass)
        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"
        entry = entity_registry.async_get(entity_id)

        assert entry is not None
        assert entry.unique_id == f"{mock_config_entry.entry_id}_{TEST_AREA_LIVING_ROOM}_occupancy"

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_initial_state_inactive(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test initial state is inactive when no occupancy detected."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"
        state = hass.states.get(entity_id)

        assert state is not None
        assert state.state == "inactive"
        assert state.attributes.get("is_occupied") is False
        assert state.attributes.get("is_active") is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_state_becomes_occupied(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test state becomes occupied when sensor triggers."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # Trigger occupancy sensor
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)

        assert state.state == "occupied"
        assert state.attributes.get("is_occupied") is True
        # Not yet active (hasn't been occupied long enough)
        assert state.attributes.get("is_active") is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_occupied_sensors_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test occupied_sensors attribute includes correct sensors."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # Trigger occupancy sensor
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        occupied_sensors = state.attributes.get("occupied_sensors", [])

        assert len(occupied_sensors) >= 1
        sensor_ids = [s.get("entity_id") for s in occupied_sensors]
        assert TEST_MOTION_SENSOR_1 in sensor_ids

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_time_until_active_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test time_until_active_minutes attribute when occupied."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # Trigger occupancy
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)

        # Should have a countdown
        time_until_active = state.attributes.get("time_until_active_minutes")
        assert time_until_active is not None
        assert time_until_active > 0

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_state_becomes_inactive_when_unoccupied(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test state becomes inactive when occupancy ends."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # Trigger then clear occupancy
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_OFF, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)

        assert state.state == "inactive"
        assert state.attributes.get("is_occupied") is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_area_attributes(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test area_id and area_name attributes."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"
        state = hass.states.get(entity_id)

        assert state.attributes.get("area_id") == TEST_AREA_LIVING_ROOM
        assert state.attributes.get("area_name") is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_min_occupancy_minutes_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test min_occupancy_minutes attribute is present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_living_room_occupancy"
        state = hass.states.get(entity_id)

        assert "min_occupancy_minutes" in state.attributes

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_grace_period_minutes_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test grace_period_minutes attribute is present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.test_thermostat_contact_sensors_living_room_occupancy"
        state = hass.states.get(entity_id)

        assert "grace_period_minutes" in state.attributes
        # Default value should be 5
        assert state.attributes.get("grace_period_minutes") == 5

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_grace_period_attributes_when_not_in_grace_period(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test grace period attributes when area is not in grace period."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.test_thermostat_contact_sensors_living_room_occupancy"
        state = hass.states.get(entity_id)

        # Should have grace period attributes but set to False/None
        assert state.attributes.get("is_in_grace_period") is False
        assert state.attributes.get("time_until_inactive_minutes") is None
        assert state.attributes.get("unoccupied_since") is None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_active_area_enters_grace_period_when_unoccupied(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that an active area enters grace period when unoccupied."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # First turn the sensor ON so we can later turn it OFF
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        # Get the coordinator from runtime_data
        coordinator = mock_config_entry.runtime_data
        area = coordinator.occupancy_tracker.get_area(TEST_AREA_LIVING_ROOM)
        now = dt_util.utcnow()

        # Back-date the occupancy to make it active
        area.occupancy_start_time = now - timedelta(minutes=10)
        area.is_active = True

        # Update coordinator to reflect active state
        coordinator.async_set_updated_data(None)
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state.state == "active"

        # Now turn the sensor OFF to trigger unoccupied state change
        # This should enter grace period
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_OFF, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)

        # Should still be "active" but in grace period
        assert state.state == "active"
        assert state.attributes.get("is_occupied") is False
        assert state.attributes.get("is_active") is True
        assert state.attributes.get("is_in_grace_period") is True
        assert state.attributes.get("time_until_inactive_minutes") is not None
        assert state.attributes.get("unoccupied_since") is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_reoccupancy_during_grace_period_clears_grace_period(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that re-occupancy during grace period clears grace period state."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = "sensor.test_thermostat_contact_sensors_living_room_occupancy"

        # First turn sensor ON
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        # Get the coordinator from runtime_data
        coordinator = mock_config_entry.runtime_data
        area = coordinator.occupancy_tracker.get_area(TEST_AREA_LIVING_ROOM)
        now = dt_util.utcnow()

        # Back-date the occupancy to make it active
        area.occupancy_start_time = now - timedelta(minutes=10)
        area.is_active = True

        # Turn off sensor (enter grace period)
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_OFF, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)
        assert state.attributes.get("is_in_grace_period") is True

        # Turn on sensor again (re-occupy during grace period)
        hass.states.async_set(TEST_MOTION_SENSOR_1, STATE_ON, {"friendly_name": "Motion Sensor"})
        await hass.async_block_till_done()

        state = hass.states.get(entity_id)

        # Should be active and NOT in grace period
        assert state.state == "active"
        assert state.attributes.get("is_occupied") is True
        assert state.attributes.get("is_active") is True
        assert state.attributes.get("is_in_grace_period") is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)


# =============================================================================
# Thermostat Control Sensor Tests
# =============================================================================


class TestThermostatControlSensor:
    """Tests for the ThermostatControlSensor class."""

    async def test_sensor_creation(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test thermostat control sensor is created."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert state is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_sensor_unique_id(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test sensor has correct unique ID."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_registry = er.async_get(hass)
        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        entry = entity_registry.async_get(entity_id)

        assert entry is not None
        assert entry.unique_id == f"{mock_config_entry.entry_id}_thermostat_control"

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_initial_state_idle(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test initial state is idle when no active rooms."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        # Should be idle since no rooms are active
        assert state.state == "idle"
        assert state.attributes.get("active_room_count") == 0

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_thermostat_entity_id_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test thermostat_entity_id attribute is present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert state.attributes.get("thermostat_entity_id") == TEST_THERMOSTAT

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_paused_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test state becomes paused when contact sensor triggers pause."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Get coordinator and manually set paused
        coordinator = mock_config_entry.runtime_data
        coordinator.is_paused = True
        coordinator.async_set_updated_data(None)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert state.state == "paused"
        assert state.attributes.get("paused_by_contact_sensors") is True

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_hvac_mode_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test hvac_mode attribute reflects thermostat state."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        # Should have hvac_mode attribute
        assert "hvac_mode" in state.attributes

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_room_count_attributes(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test active_room_count and satiated_room_count attributes."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert "active_room_count" in state.attributes
        assert "satiated_room_count" in state.attributes
        assert "all_active_rooms_satiated" in state.attributes

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_cycle_protection_attributes(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test cycle protection attributes are present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert "can_turn_on" in state.attributes
        assert "can_turn_off" in state.attributes
        assert "can_turn_on_reason" in state.attributes
        assert "can_turn_off_reason" in state.attributes

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_recommended_action_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test recommended_action attribute is present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        # Should have recommended_action or action_reason
        assert "action_reason" in state.attributes

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_room_summary_attribute(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test room_summary attribute is present."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_id = f"sensor.test_thermostat_contact_sensors_thermostat_control"
        state = hass.states.get(entity_id)

        assert "room_summary" in state.attributes
        assert isinstance(state.attributes.get("room_summary"), dict)

        await hass.config_entries.async_unload(mock_config_entry.entry_id)


# =============================================================================
# Integration Tests
# =============================================================================


class TestSensorIntegration:
    """Integration tests for sensors working together."""

    async def test_all_sensors_created(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test all expected sensors are created."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Open sensors
        open_sensors = hass.states.get(
            "sensor.test_thermostat_contact_sensors_open_sensors"
        )
        assert open_sensors is not None

        # Thermostat control
        thermostat_control = hass.states.get(
            "sensor.test_thermostat_contact_sensors_thermostat_control"
        )
        assert thermostat_control is not None

        # Room occupancy sensors (one per enabled area)
        living_room = hass.states.get(
            "sensor.test_thermostat_contact_sensors_living_room_occupancy"
        )
        assert living_room is not None

        bedroom = hass.states.get(
            "sensor.test_thermostat_contact_sensors_bedroom_occupancy"
        )
        assert bedroom is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_sensors_share_device(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test all sensors are under the same device."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        entity_registry = er.async_get(hass)

        open_sensors = entity_registry.async_get(
            "sensor.test_thermostat_contact_sensors_open_sensors"
        )
        thermostat_control = entity_registry.async_get(
            "sensor.test_thermostat_contact_sensors_thermostat_control"
        )
        living_room = entity_registry.async_get(
            "sensor.test_thermostat_contact_sensors_living_room_occupancy"
        )

        # All should have the same device_id
        assert open_sensors.device_id is not None
        assert open_sensors.device_id == thermostat_control.device_id
        assert open_sensors.device_id == living_room.device_id

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
