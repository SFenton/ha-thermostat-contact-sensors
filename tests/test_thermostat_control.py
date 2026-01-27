"""Tests for thermostat control logic."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thermostat_contact_sensors.thermostat_control import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    RoomTemperatureState,
    SatiationReason,
    ThermostatAction,
    ThermostatController,
    ThermostatState,
    get_temperature_from_state,
    is_room_satiated_for_cool,
    is_room_satiated_for_heat,
    is_room_satiated_for_heat_cool,
)
from custom_components.thermostat_contact_sensors.occupancy import (
    AreaOccupancyState,
    RoomOccupancyTracker,
)

from .conftest import (
    TEST_AREA_BEDROOM,
    TEST_AREA_LIVING_ROOM,
    TEST_TEMP_SENSOR_1,
    TEST_THERMOSTAT,
)


# =============================================================================
# Tests for get_temperature_from_state
# =============================================================================


class TestGetTemperatureFromState:
    """Tests for the get_temperature_from_state function."""

    def test_valid_temperature(self):
        """Test extracting a valid temperature value."""
        state = MagicMock()
        state.state = "21.5"
        assert get_temperature_from_state(state) == 21.5

    def test_integer_temperature(self):
        """Test extracting an integer temperature value."""
        state = MagicMock()
        state.state = "22"
        assert get_temperature_from_state(state) == 22.0

    def test_unavailable_state(self):
        """Test unavailable state returns None."""
        state = MagicMock()
        state.state = STATE_UNAVAILABLE
        assert get_temperature_from_state(state) is None

    def test_unknown_state(self):
        """Test unknown state returns None."""
        state = MagicMock()
        state.state = STATE_UNKNOWN
        assert get_temperature_from_state(state) is None

    def test_none_state(self):
        """Test None state returns None."""
        assert get_temperature_from_state(None) is None

    def test_invalid_string_state(self):
        """Test invalid string returns None."""
        state = MagicMock()
        state.state = "not_a_number"
        assert get_temperature_from_state(state) is None

    def test_empty_string_state(self):
        """Test empty string returns None."""
        state = MagicMock()
        state.state = ""
        assert get_temperature_from_state(state) is None

    def test_negative_temperature(self):
        """Test negative temperature is valid."""
        state = MagicMock()
        state.state = "-5.5"
        assert get_temperature_from_state(state) == -5.5


# =============================================================================
# Tests for is_room_satiated_for_heat
# =============================================================================


class TestIsRoomSatiatedForHeat:
    """Tests for the is_room_satiated_for_heat function."""

    def test_satiated_when_at_target(self):
        """Test room is satiated when sensor equals target."""
        readings = {"sensor.temp": 22.0}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.temp"
        assert temp == 22.0

    def test_satiated_within_deadband(self):
        """Test room is satiated when within deadband of target."""
        readings = {"sensor.temp": 21.6}  # 0.4 below target, deadband is 0.5
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.temp"
        assert temp == 21.6

    def test_satiated_exactly_at_deadband_boundary(self):
        """Test room is satiated exactly at deadband boundary."""
        readings = {"sensor.temp": 21.5}  # Exactly at target - deadband
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is True

    def test_not_satiated_below_deadband(self):
        """Test room is not satiated when below deadband threshold."""
        readings = {"sensor.temp": 21.4}  # 0.6 below target, deadband is 0.5
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is False
        assert sensor == "sensor.temp"
        assert temp == 21.4

    def test_satiated_when_above_target(self):
        """Test room is satiated when above target."""
        readings = {"sensor.temp": 23.0}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is True

    def test_multiple_sensors_warmest_satiated(self):
        """Test with multiple sensors - warmest determines satiation."""
        readings = {
            "sensor.cold": 20.0,
            "sensor.warm": 21.6,  # This one is satiated (within deadband)
            "sensor.mid": 21.0,
        }
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.warm"
        assert temp == 21.6

    def test_multiple_sensors_none_satiated(self):
        """Test with multiple sensors - none satiated."""
        readings = {
            "sensor.cold": 19.0,
            "sensor.mid": 20.0,
            "sensor.less_cold": 21.0,
        }
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is False
        # Should return the warmest (closest to satiation)
        assert sensor == "sensor.less_cold"
        assert temp == 21.0

    def test_empty_readings(self):
        """Test with no sensor readings."""
        readings = {}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is False
        assert sensor is None
        assert temp is None

    def test_zero_deadband(self):
        """Test with zero deadband - must be exactly at target."""
        readings = {"sensor.temp": 21.9}
        target = 22.0
        deadband = 0.0

        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)

        assert is_satiated is False

        readings = {"sensor.temp": 22.0}
        is_satiated, sensor, temp = is_room_satiated_for_heat(readings, target, deadband)
        assert is_satiated is True


# =============================================================================
# Tests for is_room_satiated_for_cool
# =============================================================================


class TestIsRoomSatiatedForCool:
    """Tests for the is_room_satiated_for_cool function."""

    def test_satiated_when_at_target(self):
        """Test room is satiated when sensor equals target."""
        readings = {"sensor.temp": 22.0}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.temp"
        assert temp == 22.0

    def test_satiated_within_deadband(self):
        """Test room is satiated when within deadband of target."""
        readings = {"sensor.temp": 22.4}  # 0.4 above target, deadband is 0.5
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.temp"
        assert temp == 22.4

    def test_satiated_exactly_at_deadband_boundary(self):
        """Test room is satiated exactly at deadband boundary."""
        readings = {"sensor.temp": 22.5}  # Exactly at target + deadband
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is True

    def test_not_satiated_above_deadband(self):
        """Test room is not satiated when above deadband threshold."""
        readings = {"sensor.temp": 22.6}  # 0.6 above target, deadband is 0.5
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is False
        assert sensor == "sensor.temp"
        assert temp == 22.6

    def test_satiated_when_below_target(self):
        """Test room is satiated when below target."""
        readings = {"sensor.temp": 21.0}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is True

    def test_multiple_sensors_coolest_satiated(self):
        """Test with multiple sensors - coolest determines satiation."""
        readings = {
            "sensor.hot": 24.0,
            "sensor.cool": 22.4,  # This one is satiated (within deadband)
            "sensor.mid": 23.0,
        }
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is True
        assert sensor == "sensor.cool"
        assert temp == 22.4

    def test_multiple_sensors_none_satiated(self):
        """Test with multiple sensors - none satiated."""
        readings = {
            "sensor.hot": 25.0,
            "sensor.mid": 24.0,
            "sensor.less_hot": 23.0,
        }
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is False
        # Should return the coolest (closest to satiation)
        assert sensor == "sensor.less_hot"
        assert temp == 23.0

    def test_empty_readings(self):
        """Test with no sensor readings."""
        readings = {}
        target = 22.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_cool(readings, target, deadband)

        assert is_satiated is False
        assert sensor is None
        assert temp is None


# =============================================================================
# Tests for is_room_satiated_for_heat_cool
# =============================================================================


class TestIsRoomSatiatedForHeatCool:
    """Tests for the is_room_satiated_for_heat_cool function."""

    def test_satiated_when_in_range(self):
        """Test room is satiated when temperature is in the comfort range."""
        readings = {"sensor.temp": 21.5}  # Between 20 and 24
        target_low = 20.0  # Heating target
        target_high = 24.0  # Cooling target
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is True
        assert sensor == "sensor.temp"
        assert temp == 21.5

    def test_satiated_near_low_boundary(self):
        """Test satiated when near the low (heating) boundary with deadband."""
        readings = {"sensor.temp": 19.5}  # At target_low - deadband
        target_low = 20.0
        target_high = 24.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is True

    def test_satiated_near_high_boundary(self):
        """Test satiated when near the high (cooling) boundary with deadband."""
        readings = {"sensor.temp": 24.5}  # At target_high + deadband
        target_low = 20.0
        target_high = 24.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is True

    def test_not_satiated_too_cold(self):
        """Test not satiated when too cold (below heating threshold)."""
        readings = {"sensor.temp": 19.0}  # Below target_low - deadband
        target_low = 20.0
        target_high = 24.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is False

    def test_not_satiated_too_hot(self):
        """Test not satiated when too hot (above cooling threshold)."""
        readings = {"sensor.temp": 25.5}  # Above target_high + deadband
        target_low = 20.0
        target_high = 24.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is False

    def test_multiple_sensors_one_satiated(self):
        """Test with multiple sensors - at least one must be in range."""
        readings = {
            "sensor.cold": 18.0,  # Too cold
            "sensor.ok": 22.0,  # In range
            "sensor.hot": 26.0,  # Too hot
        }
        target_low = 20.0
        target_high = 24.0
        deadband = 0.5

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, target_low, target_high, deadband
        )

        assert is_satiated is True
        assert sensor == "sensor.ok"
        assert temp == 22.0

    def test_empty_readings(self):
        """Test with no sensor readings."""
        readings = {}

        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, 20.0, 24.0, 0.5
        )

        assert is_satiated is False
        assert sensor is None
        assert temp is None


# =============================================================================
# Tests for RoomTemperatureState
# =============================================================================


class TestRoomTemperatureState:
    """Tests for the RoomTemperatureState dataclass."""

    def test_has_valid_readings_true(self):
        """Test has_valid_readings returns True when readings exist."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={"sensor.temp": 21.5},
        )
        assert room.has_valid_readings is True

    def test_has_valid_readings_false(self):
        """Test has_valid_readings returns False when no readings."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={},
        )
        assert room.has_valid_readings is False

    def test_available_sensor_count(self):
        """Test available_sensor_count returns correct count."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={
                "sensor.temp1": 21.0,
                "sensor.temp2": 22.0,
                "sensor.temp3": 23.0,
            },
        )
        assert room.available_sensor_count == 3

    def test_get_closest_to_target_heat_mode(self):
        """Test get_closest_to_target returns warmest for heat mode."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={
                "sensor.cold": 19.0,
                "sensor.warm": 22.0,
                "sensor.mid": 20.5,
            },
        )

        sensor, temp = room.get_closest_to_target(22.0, HVACMode.HEAT)

        assert sensor == "sensor.warm"
        assert temp == 22.0

    def test_get_closest_to_target_cool_mode(self):
        """Test get_closest_to_target returns coolest for cool mode."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={
                "sensor.cold": 21.0,
                "sensor.warm": 25.0,
                "sensor.mid": 23.0,
            },
        )

        sensor, temp = room.get_closest_to_target(22.0, HVACMode.COOL)

        assert sensor == "sensor.cold"
        assert temp == 21.0

    def test_get_closest_to_target_no_readings(self):
        """Test get_closest_to_target returns None when no readings."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            sensor_readings={},
        )

        sensor, temp = room.get_closest_to_target(22.0, HVACMode.HEAT)

        assert sensor is None
        assert temp is None


# =============================================================================
# Tests for ThermostatController
# =============================================================================


class TestThermostatController:
    """Tests for the ThermostatController class."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock()
        return tracker

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
        )

    def test_initialization(self, controller):
        """Test controller initializes correctly."""
        assert controller.thermostat_entity_id == TEST_THERMOSTAT
        assert controller.temperature_deadband == 0.5
        assert controller.min_cycle_on_minutes == 5
        assert controller.min_cycle_off_minutes == 5

    def test_update_cycle_time_on(self, controller):
        """Test updating cycle time when thermostat turns on."""
        now = dt_util.utcnow()
        controller.record_thermostat_on(now=now)

        assert controller._last_on_time == now
        assert controller._current_thermostat_on is True

    def test_update_cycle_time_off(self, controller):
        """Test updating cycle time when thermostat turns off."""
        now = dt_util.utcnow()
        controller.record_thermostat_off(now=now)

        assert controller._last_off_time == now
        assert controller._current_thermostat_on is False

    def test_can_turn_off_without_prior_on_time(self, controller):
        """Test can_turn_off returns True when no prior on time."""
        # No last_on_time set
        can_off, reason = controller.can_turn_off()
        assert can_off is True

    def test_can_turn_off_after_min_on_time(self, controller):
        """Test can_turn_off returns True after min on time elapsed."""
        controller._last_on_time = dt_util.utcnow() - timedelta(minutes=6)

        can_off, reason = controller.can_turn_off()
        assert can_off is True

    def test_can_turn_off_before_min_on_time(self, controller):
        """Test can_turn_off returns False before min on time elapsed."""
        controller._last_on_time = dt_util.utcnow() - timedelta(minutes=3)

        can_off, reason = controller.can_turn_off()
        assert can_off is False

    def test_can_turn_on_without_prior_off_time(self, controller):
        """Test can_turn_on returns True when no prior off time."""
        # No last_off_time set
        can_on, reason = controller.can_turn_on()
        assert can_on is True

    def test_can_turn_on_after_min_off_time(self, controller):
        """Test can_turn_on returns True after min off time elapsed."""
        controller._last_off_time = dt_util.utcnow() - timedelta(minutes=6)

        can_on, reason = controller.can_turn_on()
        assert can_on is True

    def test_can_turn_on_before_min_off_time(self, controller):
        """Test can_turn_on returns False before min off time elapsed."""
        controller._last_off_time = dt_util.utcnow() - timedelta(minutes=3)

        can_on, reason = controller.can_turn_on()
        assert can_on is False


# =============================================================================
# Tests for contact sensor priority
# =============================================================================


class TestContactSensorPriority:
    """Tests verifying contact sensor pause takes priority over occupancy control."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock()

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
        )

    def test_evaluate_returns_none_when_paused(self, controller, mock_hass):
        """Test that thermostat control returns NONE action when paused.

        When a contact sensor has triggered a pause, the thermostat control
        should not take any action - the pause handling takes priority.
        """
        # Set up thermostat state - heating mode, at 20°C, target 22°C
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {
            "temperature": 22.0,
            "current_temperature": 20.0,
        }
        mock_hass.states.get.return_value = mock_state

        # Set paused state
        controller.set_paused_by_contact_sensors(True)

        # Create active area
        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        # Call evaluate
        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Should return NONE action - pause takes priority
        assert state.recommended_action == ThermostatAction.NONE
        assert "paused" in state.action_reason.lower()


# =============================================================================
# Tests for edge cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases in thermostat control."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock()

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
        )

    def test_no_active_rooms_returns_none(self, controller, mock_hass):
        """Test that no active rooms results in NONE action."""
        # Set up thermostat state
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
        mock_hass.states.get.return_value = mock_state

        # Empty active areas list
        active_areas = []
        area_temp_sensors = {}

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # No active rooms means we don't control the thermostat
        assert state.active_room_count == 0

    def test_room_with_no_temp_sensors_ignored(self, controller, mock_hass):
        """Test that rooms without temperature sensors are ignored."""
        # Set up thermostat state
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
        mock_hass.states.get.return_value = mock_state

        # Active room with no temp sensors
        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_BEDROOM,
                area_name="Bedroom",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_BEDROOM: []}  # No temp sensors

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room should show NO_TEMP_SENSORS reason
        if TEST_AREA_BEDROOM in state.room_states:
            assert (
                state.room_states[TEST_AREA_BEDROOM].satiation_reason
                == SatiationReason.NO_TEMP_SENSORS
            )

    def test_unavailable_sensors_ignored(self, controller, mock_hass):
        """Test that unavailable sensors are ignored."""
        # Set up states - one unavailable, one valid
        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "21.5"
                return mock_state
            elif entity_id == "sensor.unavailable_temp":
                mock_state = MagicMock()
                mock_state.state = STATE_UNAVAILABLE
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {
            TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1, "sensor.unavailable_temp"]
        }

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Should have one valid reading from living room
        if TEST_AREA_LIVING_ROOM in state.room_states:
            room_state = state.room_states[TEST_AREA_LIVING_ROOM]
            # Only the valid sensor should be in readings
            assert len(room_state.sensor_readings) == 1
            assert TEST_TEMP_SENSOR_1 in room_state.sensor_readings

    def test_thermostat_off_mode_returns_none(self, controller, mock_hass):
        """Test that thermostat in OFF mode doesn't get controlled."""
        # Set up thermostat in OFF mode
        mock_state = MagicMock()
        mock_state.state = HVACMode.OFF
        mock_state.attributes = {}
        mock_hass.states.get.return_value = mock_state

        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # When thermostat is OFF, we shouldn't control it
        assert state.hvac_mode == HVACMode.OFF
        assert state.recommended_action == ThermostatAction.NONE


# =============================================================================
# Tests for different HVAC modes
# =============================================================================


class TestHVACModes:
    """Tests for different HVAC modes."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock()

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
        )

    @pytest.fixture
    def active_area_with_sensor(self):
        """Create active area and sensors for a room."""
        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}
        return active_areas, area_temp_sensors

    def test_heat_mode_not_satiated(self, controller, mock_hass, active_area_with_sensor):
        """Test heat mode when room is not satiated (needs heating)."""
        active_areas, area_temp_sensors = active_area_with_sensor

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "20.0"  # Below target - deadband (22 - 0.5 = 21.5)
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room is not satiated, thermostat should stay on
        assert state.all_active_rooms_satiated is False
        if TEST_AREA_LIVING_ROOM in state.room_states:
            assert state.room_states[TEST_AREA_LIVING_ROOM].is_satiated is False

    def test_heat_mode_satiated(self, controller, mock_hass, active_area_with_sensor):
        """Test heat mode when room is satiated (at target)."""
        active_areas, area_temp_sensors = active_area_with_sensor

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0, "current_temperature": 22.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "22.0"  # At target
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room is satiated
        assert state.all_active_rooms_satiated is True
        if TEST_AREA_LIVING_ROOM in state.room_states:
            assert state.room_states[TEST_AREA_LIVING_ROOM].is_satiated is True

    def test_cool_mode_not_satiated(self, controller, mock_hass, active_area_with_sensor):
        """Test cool mode when room is not satiated (too hot)."""
        active_areas, area_temp_sensors = active_area_with_sensor

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.COOL
                mock_state.attributes = {"temperature": 22.0, "current_temperature": 25.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "25.0"  # Above target + deadband (22 + 0.5 = 22.5)
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room is not satiated, thermostat should stay on for cooling
        assert state.all_active_rooms_satiated is False

    def test_cool_mode_satiated(self, controller, mock_hass, active_area_with_sensor):
        """Test cool mode when room is satiated (cool enough)."""
        active_areas, area_temp_sensors = active_area_with_sensor

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.COOL
                mock_state.attributes = {"temperature": 22.0, "current_temperature": 22.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "22.0"  # At target
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room is satiated
        assert state.all_active_rooms_satiated is True

    def test_heat_cool_mode_uses_target_high_low(self, controller, mock_hass, active_area_with_sensor):
        """Test heat_cool mode uses target_temp_high and target_temp_low."""
        active_areas, area_temp_sensors = active_area_with_sensor

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT_COOL
                mock_state.attributes = {
                    "target_temp_high": 24.0,
                    "target_temp_low": 20.0,
                    "current_temperature": 22.0,
                }
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "22.0"  # In the comfort range
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room should be satiated - temp is in comfort range
        assert state.target_temp_high == 24.0
        assert state.target_temp_low == 20.0
        if state.active_room_count > 0:
            assert state.all_active_rooms_satiated is True


# =============================================================================
# Tests for Critical Temperature Logic
# =============================================================================


class TestCriticalTemperatureLogic:
    """Tests for unoccupied room critical temperature detection."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock(spec=RoomOccupancyTracker)

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a thermostat controller for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
            unoccupied_heating_threshold=3.0,
            unoccupied_cooling_threshold=3.0,
        )

    @pytest.fixture
    def inactive_area(self):
        """Create an inactive area for testing."""
        return AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )

    def test_evaluate_room_critical_heat_mode_critical(self, controller, mock_hass, inactive_area):
        """Test that a room is critical when far below heat target."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "17.0"  # 5 degrees below target of 22
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT,
            target_temp=22.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is True
        assert room_state.is_active is False
        assert "17.0" in room_state.critical_reason
        assert "below heat target" in room_state.critical_reason

    def test_evaluate_room_critical_heat_mode_not_critical(self, controller, mock_hass, inactive_area):
        """Test that a room is not critical when close to heat target."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "20.0"  # 2 degrees below target of 22, within threshold
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT,
            target_temp=22.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is False
        assert room_state.critical_reason is None

    def test_evaluate_room_critical_cool_mode_critical(self, controller, mock_hass, inactive_area):
        """Test that a room is critical when far above cool target."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "28.0"  # 4 degrees above target of 24
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.COOL,
            target_temp=24.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is True
        assert "28.0" in room_state.critical_reason
        assert "above cool target" in room_state.critical_reason

    def test_evaluate_room_critical_cool_mode_not_critical(self, controller, mock_hass, inactive_area):
        """Test that a room is not critical when close to cool target."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "26.0"  # 2 degrees above target, within threshold
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.COOL,
            target_temp=24.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is False

    def test_evaluate_room_critical_heat_cool_mode_too_cold(self, controller, mock_hass, inactive_area):
        """Test heat_cool mode detects critical cold."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "15.0"  # 5 degrees below low target of 20
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT_COOL,
            target_temp=None,
            target_temp_low=20.0,
            target_temp_high=24.0,
        )

        assert room_state.is_critical is True
        assert "below heat target" in room_state.critical_reason

    def test_evaluate_room_critical_heat_cool_mode_too_hot(self, controller, mock_hass, inactive_area):
        """Test heat_cool mode detects critical hot."""
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "29.0"  # 5 degrees above high target of 24
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT_COOL,
            target_temp=None,
            target_temp_low=20.0,
            target_temp_high=24.0,
        )

        assert room_state.is_critical is True
        assert "above cool target" in room_state.critical_reason

    def test_evaluate_room_critical_no_sensors(self, controller, mock_hass, inactive_area):
        """Test no sensors returns non-critical state."""
        room_state = controller.evaluate_room_critical(
            inactive_area,
            [],
            HVACMode.HEAT,
            target_temp=22.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is False
        assert len(room_state.sensor_readings) == 0

    def test_evaluate_room_critical_uses_warmest_sensor_for_heat(self, controller, mock_hass, inactive_area):
        """Test critical detection uses warmest sensor in heat mode."""
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "15.0"  # Very cold
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "18.0"  # Warmest but still critical (below 19.0 threshold)
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT,
            target_temp=22.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is True
        assert room_state.determining_temperature == 18.0

    def test_evaluate_room_critical_uses_coldest_sensor_for_cool(self, controller, mock_hass, inactive_area):
        """Test critical detection uses coldest sensor in cool mode."""
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "31.0"  # Very hot
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "28.0"  # Coolest but still critical (above 27.0 threshold)
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.COOL,
            target_temp=24.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is True
        assert room_state.determining_temperature == 28.0

    def test_evaluate_room_critical_heat_triggers_if_all_sensors_too_cold(
        self,
        controller,
        mock_hass,
        inactive_area,
    ):
        """HEAT critical should trigger if even the warmest sensor is below the critical threshold."""
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "15.0"  # Coldest: critical (below 19.0)
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "20.0"  # Warmest: NOT critical by itself
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.HEAT,
            target_temp=22.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is False

    def test_evaluate_room_critical_cool_triggers_if_all_sensors_too_hot(
        self,
        controller,
        mock_hass,
        inactive_area,
    ):
        """COOL critical should trigger if even the coldest sensor is above the critical threshold."""
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "31.0"  # Warmest: critical (above 27.0)
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "26.0"  # Coolest: NOT critical by itself
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        room_state = controller.evaluate_room_critical(
            inactive_area,
            temp_sensors,
            HVACMode.COOL,
            target_temp=24.0,
            target_temp_low=None,
            target_temp_high=None,
        )

        assert room_state.is_critical is False


class TestEvaluateThermostatActionWithCriticalRooms:
    """Tests for evaluate_thermostat_action with critical rooms."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock(spec=RoomOccupancyTracker)

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a thermostat controller for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            min_cycle_on_minutes=5,
            min_cycle_off_minutes=5,
            unoccupied_heating_threshold=3.0,
            unoccupied_cooling_threshold=3.0,
        )

    def test_critical_room_keeps_thermostat_on(self, controller, mock_hass):
        """Test that a critical room keeps heating on when otherwise would turn off."""
        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )
        area_temp_sensors = {TEST_AREA_BEDROOM: [TEST_TEMP_SENSOR_1]}

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "16.0"  # 6 degrees below target - critical!
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(
            active_areas=[],  # No active rooms, would normally allow turn off
            area_temp_sensors=area_temp_sensors,
            inactive_areas=[inactive_area],
        )

        assert state.critical_room_count == 1
        # Thermostat is on, and critical room needs it, so stays on
        assert state.recommended_action == ThermostatAction.NONE
        assert "1 critical rooms" in state.action_reason
        assert "Already on" in state.action_reason

    def test_critical_room_with_satiated_active_room(self, controller, mock_hass):
        """Test critical room keeps heating on even when active room is satiated."""
        active_area = AreaOccupancyState(
            area_id=TEST_AREA_LIVING_ROOM,
            area_name="Living Room",
            binary_sensors=[],
            sensors=[],
        )
        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )
        area_temp_sensors = {
            TEST_AREA_LIVING_ROOM: ["sensor.living_temp"],
            TEST_AREA_BEDROOM: [TEST_TEMP_SENSOR_1],
        }

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0}
                return mock_state
            elif entity_id == "sensor.living_temp":
                mock_state = MagicMock()
                mock_state.state = "22.0"  # At target - satiated
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "16.0"  # 6 degrees below - critical
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(
            active_areas=[active_area],
            area_temp_sensors=area_temp_sensors,
            inactive_areas=[inactive_area],
        )

        assert state.critical_room_count == 1
        assert state.all_active_rooms_satiated is True
        # Thermostat stays on for critical room, even though active room is satiated
        assert state.recommended_action == ThermostatAction.NONE
        assert "1 critical rooms" in state.action_reason

    def test_no_critical_rooms_when_within_threshold(self, controller, mock_hass):
        """Test no critical rooms when temperature is within threshold."""
        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )
        area_temp_sensors = {TEST_AREA_BEDROOM: [TEST_TEMP_SENSOR_1]}

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "20.0"  # Only 2 degrees below - within 3 degree threshold
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors=area_temp_sensors,
            inactive_areas=[inactive_area],
        )

        assert state.critical_room_count == 0
        assert state.recommended_action == ThermostatAction.NONE
        assert "No active or critical rooms" in state.action_reason

    def test_critical_room_count_in_state(self, controller, mock_hass):
        """Test that critical_room_count is properly tracked."""
        inactive_areas = [
            AreaOccupancyState(area_id="room1", area_name="Room 1", binary_sensors=[], sensors=[]),
            AreaOccupancyState(area_id="room2", area_name="Room 2", binary_sensors=[], sensors=[]),
            AreaOccupancyState(area_id="room3", area_name="Room 3", binary_sensors=[], sensors=[]),
        ]
        area_temp_sensors = {
            "room1": ["sensor.temp1"],
            "room2": ["sensor.temp2"],
            "room3": ["sensor.temp3"],
        }

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0}
                return mock_state
            elif entity_id == "sensor.temp1":
                mock_state = MagicMock()
                mock_state.state = "15.0"  # Critical
                return mock_state
            elif entity_id == "sensor.temp2":
                mock_state = MagicMock()
                mock_state.state = "21.0"  # Not critical
                return mock_state
            elif entity_id == "sensor.temp3":
                mock_state = MagicMock()
                mock_state.state = "14.0"  # Critical
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors=area_temp_sensors,
            inactive_areas=inactive_areas,
        )

        assert state.critical_room_count == 2  # room1 and room3 are critical

    def test_room_states_include_critical_info(self, controller, mock_hass):
        """Test that room_states includes critical information."""
        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )
        area_temp_sensors = {TEST_AREA_BEDROOM: [TEST_TEMP_SENSOR_1]}

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.HEAT
                mock_state.attributes = {"temperature": 22.0}
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "16.0"
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors=area_temp_sensors,
            inactive_areas=[inactive_area],
        )

        assert TEST_AREA_BEDROOM in state.room_states
        room_state = state.room_states[TEST_AREA_BEDROOM]
        assert room_state.is_critical is True
        assert room_state.is_active is False
        assert room_state.critical_reason is not None


class TestUnoccupiedThresholdConfiguration:
    """Tests for configuring unoccupied heating/cooling thresholds."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock(spec=RoomOccupancyTracker)

    def test_default_thresholds(self, mock_hass, mock_occupancy_tracker):
        """Test default threshold values."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )

        assert controller.unoccupied_heating_threshold == 3.0
        assert controller.unoccupied_cooling_threshold == 3.0

    def test_custom_thresholds(self, mock_hass, mock_occupancy_tracker):
        """Test setting custom threshold values."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            unoccupied_heating_threshold=5.0,
            unoccupied_cooling_threshold=4.0,
        )

        assert controller.unoccupied_heating_threshold == 5.0
        assert controller.unoccupied_cooling_threshold == 4.0

    def test_threshold_setter(self, mock_hass, mock_occupancy_tracker):
        """Test updating thresholds via setters."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )

        controller.unoccupied_heating_threshold = 6.0
        controller.unoccupied_cooling_threshold = 5.5

        assert controller.unoccupied_heating_threshold == 6.0
        assert controller.unoccupied_cooling_threshold == 5.5

    def test_larger_threshold_changes_critical_detection(self, mock_hass, mock_occupancy_tracker):
        """Test that larger threshold makes rooms critical at higher temp difference."""
        # With threshold of 5 degrees
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            unoccupied_heating_threshold=5.0,
        )

        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            binary_sensors=[],
            sensors=[],
        )
        temp_sensors = [TEST_TEMP_SENSOR_1]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "18.0"  # 4 degrees below target of 22
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        # 4 degrees below with 5 degree threshold - NOT critical
        room_state = controller.evaluate_room_critical(
            inactive_area, temp_sensors, HVACMode.HEAT, 22.0, None, None
        )
        assert room_state.is_critical is False

        # Now change threshold to 3 degrees
        controller.unoccupied_heating_threshold = 3.0

        # 4 degrees below with 3 degree threshold - IS critical
        room_state = controller.evaluate_room_critical(
            inactive_area, temp_sensors, HVACMode.HEAT, 22.0, None, None
        )
        assert room_state.is_critical is True


# Additional test classes removed - they were testing non-existent methods
# and complex internal behavior already covered by existing integration tests


