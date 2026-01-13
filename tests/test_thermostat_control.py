"""Tests for thermostat control logic."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import (
    ATTR_TEMPERATURE,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
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
    determine_rooms_need_mode,
    get_temperature_from_state,
    infer_effective_hvac_mode,
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

    def test_target_temperature_field(self):
        """Test that target_temperature field is stored correctly."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
            target_temperature=22.5,
        )
        assert room.target_temperature == 22.5

    def test_target_temperature_default_none(self):
        """Test that target_temperature defaults to None."""
        room = RoomTemperatureState(
            area_id="test_area",
            area_name="Test Area",
        )
        assert room.target_temperature is None


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

    def test_no_rooms_configured_returns_none(self, controller, mock_hass):
        """Test that no rooms configured results in NONE action."""
        # Set up thermostat state
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
        mock_hass.states.get.return_value = mock_state

        # No active areas and no inactive areas = no rooms configured
        active_areas = []
        area_temp_sensors = {}
        inactive_areas = []

        state = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas
        )

        # No rooms configured means we don't control the thermostat
        assert state.active_room_count == 0
        assert state.recommended_action == ThermostatAction.NONE
        assert "no rooms configured" in state.action_reason.lower()

    def test_rooms_configured_but_none_active_turns_off(self, controller, mock_hass):
        """Test that rooms configured but none active results in TURN_OFF when thermostat is on."""
        # Set up thermostat state
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
        mock_hass.states.get.return_value = mock_state

        # No active areas but there are inactive areas (rooms are configured)
        active_areas = []
        inactive_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_active=False,
        )
        # Room temperature is comfortable, not critical
        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                temp_state = MagicMock()
                temp_state.state = "21.0"  # Within threshold
                return temp_state
            return None
        mock_hass.states.get.side_effect = get_state

        area_temp_sensors = {TEST_AREA_BEDROOM: [TEST_TEMP_SENSOR_1]}
        inactive_areas = [inactive_area]

        state = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas
        )

        # Rooms configured but none active means we should turn off the thermostat
        assert state.active_room_count == 0
        assert state.recommended_action == ThermostatAction.TURN_OFF
        # With no active rooms, we get "all 0 active rooms satiated" or "no active or critical rooms"
        assert "active" in state.action_reason.lower()

    def test_no_active_rooms_already_off_returns_none(self, controller, mock_hass):
        """Test that no active rooms with thermostat already off results in NONE action."""
        # Set up thermostat state as off (by user, not us)
        mock_state = MagicMock()
        mock_state.state = HVACMode.OFF
        mock_state.attributes = {"temperature": 22.0, "current_temperature": 20.0}
        mock_hass.states.get.return_value = mock_state

        # No active areas, no inactive areas = no rooms configured
        active_areas = []
        area_temp_sensors = {}

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Already off by user choice, should be NONE
        assert state.active_room_count == 0
        assert state.recommended_action == ThermostatAction.NONE

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
        """Test critical detection uses warmest sensor (most favorable) in heat mode.
        
        With warmest sensor at 21°, the room is NOT critical because the warmest
        spot is close enough to target. Only critical if even the warmest spot is too cold.
        """
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "21.0"  # Warm, most favorable
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "16.0"  # Cold spot
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

        # NOT critical because warmest sensor (21°) is within threshold of target (22°)
        assert room_state.is_critical is False
        assert room_state.determining_temperature == 21.0

    def test_evaluate_room_critical_uses_coolest_sensor_for_cool(self, controller, mock_hass, inactive_area):
        """Test critical detection uses coolest sensor (most favorable) in cool mode.
        
        With coolest sensor at 25°, the room is NOT critical because the coolest
        spot is close enough to target. Only critical if even the coolest spot is too hot.
        """
        temp_sensors = [TEST_TEMP_SENSOR_1, "sensor.other_temp"]

        def get_state(entity_id):
            if entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "25.0"  # Cool, most favorable
                return mock_state
            elif entity_id == "sensor.other_temp":
                mock_state = MagicMock()
                mock_state.state = "30.0"  # Hot spot
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

        # NOT critical because coolest sensor (25°) is within threshold of target (24°)
        assert room_state.is_critical is False
        assert room_state.determining_temperature == 25.0


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
        # Should turn off when idle (no active or critical rooms) and thermostat is on
        assert state.recommended_action == ThermostatAction.TURN_OFF
        # With no active rooms and no critical rooms, we turn off
        assert "active" in state.action_reason.lower() or "satiated" in state.action_reason.lower()

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


# =============================================================================
# Tests for integration vs user turn-off tracking
# =============================================================================


class TestWeTurnedOffFlag:
    """Tests for _we_turned_off flag behavior."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        hass.services = AsyncMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        tracker.active_areas = []
        return tracker

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )

    def test_we_turned_off_initially_false(self, controller):
        """Test that _we_turned_off is initially False."""
        assert controller._we_turned_off is False

    def test_thermostat_off_treated_as_user_choice_when_flag_false(self, controller, mock_hass):
        """Test that thermostat OFF is treated as user choice when we didn't turn it off and respect_user_off is True."""
        mock_state = MagicMock()
        mock_state.state = HVACMode.OFF
        mock_state.attributes = {}
        mock_hass.states.get.return_value = mock_state

        # Ensure flag is False (user turned it off)
        controller._we_turned_off = False

        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        # With respect_user_off=True (default), user's off choice is respected
        state = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, respect_user_off=True
        )

        assert state.hvac_mode == HVACMode.OFF
        assert state.recommended_action == ThermostatAction.NONE
        assert "user choice" in state.action_reason.lower()

    def test_thermostat_off_overridden_when_respect_user_off_false(
        self, controller, mock_hass
    ):
        """Test that thermostat OFF can be overridden when respect_user_off is False."""

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.OFF
                mock_state.attributes = {
                    ATTR_TEMPERATURE: 72.0,
                }
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                # Temperature below target - not satiated, should want to turn on
                mock_state = MagicMock()
                mock_state.state = "68.0"
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        # Ensure flag is False (user turned it off)
        controller._we_turned_off = False
        # Set stored target temp so we have a target to evaluate against
        controller._stored_target_temp = 72.0

        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        # With respect_user_off=False, integration can turn thermostat back on
        state = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, respect_user_off=False
        )

        # Should recommend turning on since room is not satiated
        # (User's off is not respected)
        assert state.recommended_action == ThermostatAction.TURN_ON
        assert "user choice" not in state.action_reason.lower()

    def test_thermostat_off_continues_evaluation_when_we_turned_off(
        self, controller, mock_hass
    ):
        """Test that thermostat OFF continues evaluation when we turned it off."""

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.OFF
                mock_state.attributes = {
                    ATTR_TEMPERATURE: 72.0,
                }
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                # Temperature below target - not satiated, should want to turn on
                mock_state = MagicMock()
                mock_state.state = "68.0"
                return mock_state
            return None

        mock_hass.states.get.side_effect = get_state

        # Set flag to True (we turned it off) and previous mode
        controller._we_turned_off = True
        controller._previous_hvac_mode = HVACMode.HEAT.value

        active_areas = [
            AreaOccupancyState(
                area_id=TEST_AREA_LIVING_ROOM,
                area_name="Living Room",
                is_active=True,
            )
        ]
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Should recommend turning on since room is not satiated
        # (We don't treat our own turn-off as user choice)
        assert state.recommended_action == ThermostatAction.TURN_ON

    @pytest.mark.asyncio
    async def test_execute_turn_off_sets_flag(self, controller, mock_hass):
        """Test that executing TURN_OFF action sets _we_turned_off flag."""
        # Set up thermostat state
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_hass.states.get.return_value = mock_state

        # Create a thermostat state with TURN_OFF action
        from custom_components.thermostat_contact_sensors.thermostat_control import (
            ThermostatState,
        )

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_OFF
        thermostat_state.action_reason = "All rooms satiated"

        # Ensure flag starts False
        controller._we_turned_off = False

        # Execute the action
        result = await controller.async_execute_action(thermostat_state)

        assert result is True
        assert controller._we_turned_off is True

    @pytest.mark.asyncio
    async def test_execute_turn_on_clears_flag(self, controller, mock_hass):
        """Test that executing TURN_ON action clears _we_turned_off flag."""
        mock_hass.states.get.return_value = None

        # Create a thermostat state with TURN_ON action
        from custom_components.thermostat_contact_sensors.thermostat_control import (
            ThermostatState,
        )

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.hvac_mode = HVACMode.OFF
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs heating"

        # Set previous mode so we have something to restore to
        controller._previous_hvac_mode = HVACMode.HEAT.value

        # Set flag to True first
        controller._we_turned_off = True

        # Execute the action
        result = await controller.async_execute_action(thermostat_state)

        assert result is True
        assert controller._we_turned_off is False

    def test_we_turned_off_in_diagnostics(self, controller, mock_hass):
        """Test that _we_turned_off appears in diagnostics output."""
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {ATTR_TEMPERATURE: 72.0}
        mock_hass.states.get.return_value = mock_state

        controller._we_turned_off = True

        summary = controller.get_summary([], {})

        assert "we_turned_off" in summary
        assert summary["we_turned_off"] is True


class TestThermostatControllerPersistence:
    """Tests for thermostat controller state persistence."""

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        return tracker

    def test_controller_without_entry_id_has_no_store(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test controller without entry_id has no storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )
        assert controller._store is None

    def test_controller_with_entry_id_has_store(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test controller with entry_id creates storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            entry_id="test_entry_123",
        )
        assert controller._store is not None

    @pytest.mark.asyncio
    async def test_async_setup_restores_state(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that async_setup restores state from storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            entry_id="test_entry_123",
        )

        # Mock the store's async_load
        controller._store.async_load = AsyncMock(
            return_value={
                "we_turned_off": True,
                "previous_hvac_mode": "heat",
                "saved_at": "2025-01-01T00:00:00",
            }
        )

        assert controller._we_turned_off is False
        assert controller._previous_hvac_mode is None

        await controller.async_setup()

        assert controller._we_turned_off is True
        assert controller._previous_hvac_mode == "heat"

    @pytest.mark.asyncio
    async def test_async_shutdown_saves_state(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that async_shutdown saves state to storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            entry_id="test_entry_123",
        )

        controller._store.async_save = AsyncMock()
        controller._we_turned_off = True
        controller._previous_hvac_mode = "cool"

        await controller.async_shutdown()

        controller._store.async_save.assert_called_once()
        saved_data = controller._store.async_save.call_args[0][0]
        assert saved_data["we_turned_off"] is True
        assert saved_data["previous_hvac_mode"] == "cool"
        assert "saved_at" in saved_data

    @pytest.mark.asyncio
    async def test_async_setup_without_store_does_not_fail(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that async_setup works when there's no store."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            # No entry_id, so no store
        )

        # Should not raise
        await controller.async_setup()
        assert controller._we_turned_off is False

    @pytest.mark.asyncio
    async def test_async_shutdown_without_store_does_not_fail(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that async_shutdown works when there's no store."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            # No entry_id, so no store
        )

        # Should not raise
        await controller.async_shutdown()


class TestStoredTargetTemperatures:
    """Tests for stored target temperature behavior."""

    @pytest.fixture
    def mock_hass(self):
        """Create a mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        hass.services = AsyncMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        tracker.active_areas = []
        return tracker

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController for testing."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )

    def test_stored_target_temps_initially_none(self, controller):
        """Test that stored target temps are initially None."""
        assert controller._stored_target_temp is None
        assert controller._stored_target_temp_low is None
        assert controller._stored_target_temp_high is None

    def test_target_temps_stored_when_thermostat_on(self, controller, mock_hass):
        """Test that target temps are stored when retrieved while thermostat is ON."""
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_state.attributes = {ATTR_TEMPERATURE: 72.0}
        mock_hass.states.get.return_value = mock_state

        target, low, high = controller.get_target_temperatures()

        assert target == 72.0
        assert controller._stored_target_temp == 72.0

    def test_stored_temps_used_when_off_and_we_turned_off(self, controller, mock_hass):
        """Test stored temps are returned when thermostat is OFF and we turned it off."""
        # First, get temps while ON to store them
        mock_state_on = MagicMock()
        mock_state_on.state = HVACMode.HEAT
        mock_state_on.attributes = {ATTR_TEMPERATURE: 72.0}
        mock_hass.states.get.return_value = mock_state_on

        controller.get_target_temperatures()  # This stores the value

        # Now thermostat is OFF and we turned it off
        mock_state_off = MagicMock()
        mock_state_off.state = HVACMode.OFF
        mock_state_off.attributes = {}  # No target temp when OFF
        mock_hass.states.get.return_value = mock_state_off
        controller._we_turned_off = True

        target, low, high = controller.get_target_temperatures()

        # Should return stored value
        assert target == 72.0

    def test_stored_temps_used_when_off_even_if_user_turned_off(self, controller, mock_hass):
        """Test stored temps are returned when user turned off thermostat (for display)."""
        # First, get temps while ON to store them
        mock_state_on = MagicMock()
        mock_state_on.state = HVACMode.HEAT
        mock_state_on.attributes = {ATTR_TEMPERATURE: 72.0}
        mock_hass.states.get.return_value = mock_state_on

        controller.get_target_temperatures()  # This stores the value

        # Now thermostat is OFF but user turned it off (not us)
        mock_state_off = MagicMock()
        mock_state_off.state = HVACMode.OFF
        mock_state_off.attributes = {}  # No target temp when OFF
        mock_hass.states.get.return_value = mock_state_off
        controller._we_turned_off = False  # User turned it off

        target, low, high = controller.get_target_temperatures()

        # Should return stored value for display purposes (virtual thermostats, sensors)
        # even when user turned it off - we always need targets for display
        assert target == 72.0

    def test_stored_temps_for_heat_cool_mode(self, controller, mock_hass):
        """Test that heat_cool temps (low/high) are stored and restored."""
        # Store temps while in HEAT_COOL mode
        mock_state_on = MagicMock()
        mock_state_on.state = HVACMode.HEAT_COOL
        mock_state_on.attributes = {
            ATTR_TARGET_TEMP_LOW: 68.0,
            ATTR_TARGET_TEMP_HIGH: 75.0,
        }
        mock_hass.states.get.return_value = mock_state_on

        controller.get_target_temperatures()

        assert controller._stored_target_temp_low == 68.0
        assert controller._stored_target_temp_high == 75.0

        # Now OFF with we_turned_off
        mock_state_off = MagicMock()
        mock_state_off.state = HVACMode.OFF
        mock_state_off.attributes = {}
        mock_hass.states.get.return_value = mock_state_off
        controller._we_turned_off = True

        target, low, high = controller.get_target_temperatures()

        assert low == 68.0
        assert high == 75.0

    def test_evaluation_uses_stored_temps_when_off(self, controller, mock_hass):
        """Test that satiation evaluation uses stored temps when thermostat is OFF."""
        # Set up: thermostat was HEAT with target 72, room at 68
        controller._stored_target_temp = 72.0
        controller._we_turned_off = True
        controller._previous_hvac_mode = HVACMode.HEAT.value

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                mock_state = MagicMock()
                mock_state.state = HVACMode.OFF
                mock_state.attributes = {}  # No target temp when OFF
                return mock_state
            elif entity_id == TEST_TEMP_SENSOR_1:
                mock_state = MagicMock()
                mock_state.state = "68.0"  # Below target
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
        area_temp_sensors = {TEST_AREA_LIVING_ROOM: [TEST_TEMP_SENSOR_1]}

        state = controller.evaluate_thermostat_action(active_areas, area_temp_sensors)

        # Room is at 68, target is 72, so NOT satiated - should want to turn ON
        assert state.recommended_action == ThermostatAction.TURN_ON


class TestStoredTargetTempsPersistence:
    """Tests for persistence of stored target temperatures."""

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        return tracker

    @pytest.mark.asyncio
    async def test_stored_temps_are_saved(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that stored target temps are saved to storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            entry_id="test_entry_123",
        )

        controller._store.async_save = AsyncMock()
        controller._stored_target_temp = 72.0
        controller._stored_target_temp_low = 68.0
        controller._stored_target_temp_high = 76.0

        await controller.async_shutdown()

        controller._store.async_save.assert_called_once()
        saved_data = controller._store.async_save.call_args[0][0]
        assert saved_data["stored_target_temp"] == 72.0
        assert saved_data["stored_target_temp_low"] == 68.0
        assert saved_data["stored_target_temp_high"] == 76.0

    @pytest.mark.asyncio
    async def test_stored_temps_are_restored(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that stored target temps are restored from storage."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            entry_id="test_entry_123",
        )

        controller._store.async_load = AsyncMock(
            return_value={
                "we_turned_off": True,
                "previous_hvac_mode": "heat",
                "stored_target_temp": 72.0,
                "stored_target_temp_low": 68.0,
                "stored_target_temp_high": 76.0,
                "saved_at": "2025-01-01T00:00:00",
            }
        )

        await controller.async_setup()

        assert controller._stored_target_temp == 72.0
        assert controller._stored_target_temp_low == 68.0
        assert controller._stored_target_temp_high == 76.0


# =============================================================================
# Tests for Area-Specific Target Temperatures
# =============================================================================


class TestAreaSpecificTargets:
    """Tests for area-specific virtual thermostat target temperatures."""

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        tracker = MagicMock()
        return tracker

    @pytest.fixture
    def mock_area_thermostat(self):
        """Create a mock area virtual thermostat."""
        thermostat = MagicMock()
        thermostat.target_temperature_low = 72.0
        thermostat.target_temperature_high = 79.0
        return thermostat

    @pytest.fixture
    def mock_area_thermostats(self, mock_area_thermostat):
        """Create a dict of mock area thermostats."""
        living_room = MagicMock()
        living_room.target_temperature_low = 71.0
        living_room.target_temperature_high = 78.0
        living_room.effective_target_temp_low = 71.0
        living_room.effective_target_temp_high = 78.0

        office = MagicMock()
        office.target_temperature_low = 72.0
        office.target_temperature_high = 79.0
        office.effective_target_temp_low = 72.0
        office.effective_target_temp_high = 79.0

        music_room = MagicMock()
        music_room.target_temperature_low = 71.0
        music_room.target_temperature_high = 78.0
        music_room.effective_target_temp_low = 71.0
        music_room.effective_target_temp_high = 78.0

        return {
            "living_room": living_room,
            "office": office,
            "music_room": music_room,
        }

    def test_get_area_target_temperatures_from_virtual_thermostat(
        self, hass: HomeAssistant, mock_occupancy_tracker, mock_area_thermostats
    ):
        """Test that area targets come from virtual thermostat when available."""
        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: mock_area_thermostats,
        )

        # Office has 72/79
        target, low, high = controller.get_area_target_temperatures("office")
        assert low == 72.0
        assert high == 79.0
        assert target == 75.5  # Average of 72 and 79

        # Living room has 71/78
        target, low, high = controller.get_area_target_temperatures("living_room")
        assert low == 71.0
        assert high == 78.0
        assert target == 74.5  # Average

    def test_get_area_target_temperatures_falls_back_to_physical(
        self, hass: HomeAssistant, mock_occupancy_tracker, mock_area_thermostats
    ):
        """Test fallback to physical thermostat for unknown areas."""
        # Set up physical thermostat state
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT_COOL,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT_COOL,
                ATTR_TARGET_TEMP_LOW: 70.0,
                ATTR_TARGET_TEMP_HIGH: 76.0,
            },
        )

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: mock_area_thermostats,
        )

        # Unknown area should fall back to physical thermostat
        target, low, high = controller.get_area_target_temperatures("unknown_area")
        assert low == 70.0
        assert high == 76.0

    def test_get_area_target_temperatures_no_getter(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that no getter falls back to physical thermostat."""
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT_COOL,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT_COOL,
                ATTR_TARGET_TEMP_LOW: 68.0,
                ATTR_TARGET_TEMP_HIGH: 74.0,
            },
        )

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            # No area_thermostats_getter
        )

        target, low, high = controller.get_area_target_temperatures("living_room")
        assert low == 68.0
        assert high == 74.0

    @pytest.mark.asyncio
    async def test_room_satiation_uses_area_specific_targets(
        self, hass: HomeAssistant, mock_occupancy_tracker, mock_area_thermostats
    ):
        """Test that room satiation evaluation uses area-specific targets."""
        # Set up temperature sensors
        hass.states.async_set("sensor.living_room_temp", "70.0")
        hass.states.async_set("sensor.office_temp", "71.0")

        # Physical thermostat at 71/78
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT_COOL,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT_COOL,
                ATTR_TARGET_TEMP_LOW: 71.0,
                ATTR_TARGET_TEMP_HIGH: 78.0,
            },
        )

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: mock_area_thermostats,
            temperature_deadband=0.5,
        )

        # Create active areas
        living_room_area = AreaOccupancyState(
            area_id="living_room",
            area_name="Living Room",
        )
        office_area = AreaOccupancyState(
            area_id="office",
            area_name="Office",
        )

        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "office": ["sensor.office_temp"],
        }

        # Evaluate
        state = controller.evaluate_thermostat_action(
            active_areas=[living_room_area, office_area],
            area_temp_sensors=area_temp_sensors,
            inactive_areas=[],
        )

        # Living room: 70°F, target 71°F (deadband 0.5) -> satiated at 70.5+
        # With 0.5 deadband, satiated when >= 70.5, room is at 70 -> NOT satiated
        living_room_state = state.room_states["living_room"]
        assert living_room_state.is_satiated is False

        # Office: 71°F, target 72°F (deadband 0.5) -> satiated at 71.5+
        # Room is at 71 -> NOT satiated (needs 71.5)
        office_state = state.room_states["office"]
        assert office_state.is_satiated is False

    @pytest.mark.asyncio
    async def test_different_rooms_different_satiation_with_same_temp(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test rooms at same temp but different targets have different satiation."""
        # Both rooms at 71°F
        hass.states.async_set("sensor.living_room_temp", "71.0")
        hass.states.async_set("sensor.office_temp", "71.0")

        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT_COOL,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT_COOL,
                ATTR_TARGET_TEMP_LOW: 70.0,
                ATTR_TARGET_TEMP_HIGH: 78.0,
            },
        )

        # Living room: 70/78 targets (71° is satiated for heat)
        living_room_therm = MagicMock()
        living_room_therm.target_temperature_low = 70.0
        living_room_therm.target_temperature_high = 78.0
        living_room_therm.effective_target_temp_low = 70.0
        living_room_therm.effective_target_temp_high = 78.0

        # Office: 72/79 targets (71° is NOT satiated for heat)
        office_therm = MagicMock()
        office_therm.target_temperature_low = 72.0
        office_therm.target_temperature_high = 79.0
        office_therm.effective_target_temp_low = 72.0
        office_therm.effective_target_temp_high = 79.0

        area_thermostats = {
            "living_room": living_room_therm,
            "office": office_therm,
        }

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: area_thermostats,
            temperature_deadband=0.5,
        )

        living_room_area = AreaOccupancyState(
            area_id="living_room",
            area_name="Living Room",
        )
        office_area = AreaOccupancyState(
            area_id="office",
            area_name="Office",
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[living_room_area, office_area],
            area_temp_sensors={
                "living_room": ["sensor.living_room_temp"],
                "office": ["sensor.office_temp"],
            },
            inactive_areas=[],
        )

        # Living room at 71°F with target 70°F -> satiated (71 >= 70 - 0.5)
        assert state.room_states["living_room"].is_satiated is True

        # Office at 71°F with target 72°F -> NOT satiated (71 < 72 - 0.5 = 71.5)
        assert state.room_states["office"].is_satiated is False

        # Thermostat already on (HEAT_COOL mode), so action is NONE but reason shows need
        assert state.recommended_action == ThermostatAction.NONE
        assert "active rooms need conditioning" in state.action_reason

    @pytest.mark.asyncio
    async def test_critical_room_uses_area_specific_targets(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test that critical room evaluation uses area-specific targets."""
        # Music room at 67°F (inactive)
        hass.states.async_set("sensor.music_room_temp", "67.0")

        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT,
                ATTR_TEMPERATURE: 71.0,
            },
        )

        # Music room has target 71/78
        music_room_therm = MagicMock()
        music_room_therm.target_temperature_low = 71.0
        music_room_therm.target_temperature_high = 78.0
        music_room_therm.effective_target_temp_low = 71.0
        music_room_therm.effective_target_temp_high = 78.0

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: {"music_room": music_room_therm},
            unoccupied_heating_threshold=3.0,  # Critical if > 3° below target
        )

        music_room_area = AreaOccupancyState(
            area_id="music_room",
            area_name="Music Room",
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors={"music_room": ["sensor.music_room_temp"]},
            inactive_areas=[music_room_area],
        )

        # Music room at 67°F, target 71°F, threshold 3°F
        # 67 < 71 - 3 = 68 -> CRITICAL
        music_room_state = state.room_states["music_room"]
        assert music_room_state.is_critical is True
        assert "below heat target" in music_room_state.critical_reason

    @pytest.mark.asyncio
    async def test_critical_room_not_critical_with_higher_area_threshold(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test room is not critical when area target is lower."""
        # Room at 67°F
        hass.states.async_set("sensor.guest_room_temp", "67.0")

        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT,
                ATTR_TEMPERATURE: 71.0,  # Physical thermostat at 71
            },
        )

        # Guest room has lower target of 65°F (not 71)
        guest_room_therm = MagicMock()
        guest_room_therm.target_temperature_low = 65.0
        guest_room_therm.target_temperature_high = 78.0
        guest_room_therm.effective_target_temp_low = 65.0
        guest_room_therm.effective_target_temp_high = 78.0

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: {"guest_room": guest_room_therm},
            unoccupied_heating_threshold=3.0,
        )

        guest_room_area = AreaOccupancyState(
            area_id="guest_room",
            area_name="Guest Room",
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors={"guest_room": ["sensor.guest_room_temp"]},
            inactive_areas=[guest_room_area],
        )

        # Guest room at 67°F, target 65°F, threshold 3°F
        # 67 > 65 - 3 = 62 -> NOT critical
        guest_room_state = state.room_states["guest_room"]
        assert guest_room_state.is_critical is False

    @pytest.mark.asyncio
    async def test_mixed_scenario_active_and_critical(
        self, hass: HomeAssistant, mock_occupancy_tracker
    ):
        """Test scenario with active rooms needing heat and critical inactive rooms."""
        # Living room: 70°F, active
        hass.states.async_set("sensor.living_room_temp", "70.0")
        # Music room: 67°F, inactive -> critical
        hass.states.async_set("sensor.music_room_temp", "67.0")
        # Office: 72°F, active -> satiated
        hass.states.async_set("sensor.office_temp", "72.0")

        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT_COOL,
            {
                ATTR_HVAC_MODE: HVACMode.HEAT_COOL,
                ATTR_TARGET_TEMP_LOW: 71.0,
                ATTR_TARGET_TEMP_HIGH: 78.0,
            },
        )

        living_room_therm = MagicMock()
        living_room_therm.target_temperature_low = 71.0
        living_room_therm.target_temperature_high = 78.0
        living_room_therm.effective_target_temp_low = 71.0
        living_room_therm.effective_target_temp_high = 78.0

        music_room_therm = MagicMock()
        music_room_therm.target_temperature_low = 71.0
        music_room_therm.target_temperature_high = 78.0
        music_room_therm.effective_target_temp_low = 71.0
        music_room_therm.effective_target_temp_high = 78.0

        office_therm = MagicMock()
        office_therm.target_temperature_low = 71.0  # Office satiated at 72
        office_therm.target_temperature_high = 79.0
        office_therm.effective_target_temp_low = 71.0
        office_therm.effective_target_temp_high = 79.0

        area_thermostats = {
            "living_room": living_room_therm,
            "music_room": music_room_therm,
            "office": office_therm,
        }

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: area_thermostats,
            temperature_deadband=0.5,
            unoccupied_heating_threshold=3.0,
        )

        living_room_area = AreaOccupancyState(
            area_id="living_room", area_name="Living Room"
        )
        office_area = AreaOccupancyState(
            area_id="office", area_name="Office"
        )
        music_room_area = AreaOccupancyState(
            area_id="music_room", area_name="Music Room"
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[living_room_area, office_area],
            area_temp_sensors={
                "living_room": ["sensor.living_room_temp"],
                "office": ["sensor.office_temp"],
                "music_room": ["sensor.music_room_temp"],
            },
            inactive_areas=[music_room_area],
        )

        # Verify individual room states
        assert state.room_states["living_room"].is_active is True
        assert state.room_states["living_room"].is_satiated is False  # 70 < 70.5

        assert state.room_states["office"].is_active is True
        assert state.room_states["office"].is_satiated is True  # 72 >= 70.5

        assert state.room_states["music_room"].is_active is False
        assert state.room_states["music_room"].is_critical is True  # 67 < 68

        # Overall state
        assert state.active_room_count == 2
        assert state.satiated_room_count == 1
        assert state.critical_room_count == 1
        assert state.all_active_rooms_satiated is False

        # Thermostat already on (HEAT_COOL mode), so action is NONE
        assert state.recommended_action == ThermostatAction.NONE
        assert "active rooms need conditioning" in state.action_reason or "critical rooms" in state.action_reason


# =============================================================================
# Tests for Away Mode Integration with Thermostat Control
# =============================================================================


class TestThermostatControlAwayMode:
    """Tests for away mode integration with thermostat control.
    
    Note: The away mode logic is primarily handled in the climate.py module
    through the effective_target_temp_low/high properties. The thermostat
    controller uses these effective temps via get_area_target_temperatures.
    These tests verify the satiation logic works correctly with away-adjusted temps.
    """

    def test_away_mode_heating_more_permissive(self):
        """Test that away mode allows lower temps before heating kicks in."""
        # Room is at 66°F - above the away target but below home target
        # With away target of 65 (68-3) and deadband of 0.5:
        # Room should be "satiated" at 65.5 or above
        readings = {"sensor.temp": 66.0}
        is_satiated, sensor, temp = is_room_satiated_for_heat(
            readings, 65.0, 0.5  # Away mode target
        )
        assert is_satiated is True

        # Same room temp with HOME target (68) should NOT be satiated
        is_satiated_home, _, _ = is_room_satiated_for_heat(
            readings, 68.0, 0.5  # Home target
        )
        assert is_satiated_home is False

    def test_away_mode_cooling_more_permissive(self):
        """Test that away mode allows higher temps before cooling kicks in."""
        # Room is at 76°F - below the away target but above home target
        # With away target of 78 (75+3) and deadband of 0.5:
        # Room should be "satiated" at 77.5 or below
        readings = {"sensor.temp": 76.0}
        is_satiated, sensor, temp = is_room_satiated_for_cool(
            readings, 78.0, 0.5  # Away mode target
        )
        assert is_satiated is True

        # Same room temp with HOME target (75) should NOT be satiated
        is_satiated_home, _, _ = is_room_satiated_for_cool(
            readings, 75.0, 0.5  # Home target
        )
        assert is_satiated_home is False

    def test_away_mode_heat_cool_more_permissive(self):
        """Test away mode in heat_cool mode allows wider temp range."""
        # At 66°F with away targets of 65/78 (home was 68/75)
        # Heating should be satiated (66 > 65.5)
        # Cooling should be satiated (66 < 77.5)
        readings = {"sensor.temp": 66.0}
        is_satiated, sensor, temp = is_room_satiated_for_heat_cool(
            readings, 65.0, 78.0, 0.5  # Away targets
        )
        assert is_satiated is True

        # Same temp with HOME targets (68/75) should NOT be satiated for heat
        is_satiated_home, _, _ = is_room_satiated_for_heat_cool(
            readings, 68.0, 75.0, 0.5  # Home targets
        )
        assert is_satiated_home is False  # 66 < 67.5 (68 - 0.5)

    def test_get_area_temps_uses_effective_temps(self, hass: HomeAssistant):
        """Test that get_area_target_temperatures uses effective temps."""
        # Create mock virtual thermostat with away mode applied
        mock_vt = MagicMock()
        mock_vt.target_temperature_low = 68.0  # Display value
        mock_vt.target_temperature_high = 75.0  # Display value
        mock_vt.effective_target_temp_low = 65.0  # Away mode value (68 - 3)
        mock_vt.effective_target_temp_high = 78.0  # Away mode value (75 + 3)

        mock_occupancy_tracker = MagicMock()

        controller = ThermostatController(
            hass=hass,
            thermostat_entity_id="climate.test",
            occupancy_tracker=mock_occupancy_tracker,
            area_thermostats_getter=lambda: {"living_room": mock_vt},
        )

        # Get area target temps - should return effective temps
        target, low, high = controller.get_area_target_temperatures("living_room")

        # The controller uses effective_target_temp_low/high for control
        assert low == 65.0
        assert high == 78.0


# =============================================================================
# Tests for Boost Temperature Feature
# =============================================================================


class TestBoostTemperature:
    """Tests for the heating/cooling boost offset feature.

    The boost feature ensures that when turning on the thermostat, we set
    the physical thermostat's temperature target to overcome its internal
    deadband. This is especially important for thermostats like ecobee that
    may not actually call for heat/cool if they think they're at target.
    """

    @pytest.fixture
    def mock_hass(self):
        """Create a mock HomeAssistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self):
        """Create a mock occupancy tracker."""
        return MagicMock()

    @pytest.fixture
    def controller_with_heating_boost(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController with heating boost configured."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            heating_boost_offset=2.0,
            cooling_boost_offset=0.0,
        )

    @pytest.fixture
    def controller_with_cooling_boost(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController with cooling boost configured."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            heating_boost_offset=0.0,
            cooling_boost_offset=2.0,
        )

    @pytest.fixture
    def controller_with_both_boosts(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController with both boosts configured."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            heating_boost_offset=2.0,
            cooling_boost_offset=1.5,
        )

    @pytest.fixture
    def controller_no_boost(self, mock_hass, mock_occupancy_tracker):
        """Create a ThermostatController with no boost configured."""
        return ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
            heating_boost_offset=0.0,
            cooling_boost_offset=0.0,
        )

    def test_boost_properties(self, controller_with_both_boosts):
        """Test that boost offset properties are correctly set."""
        assert controller_with_both_boosts.heating_boost_offset == 2.0
        assert controller_with_both_boosts.cooling_boost_offset == 1.5

    def test_boost_properties_can_be_updated(self, controller_no_boost):
        """Test that boost offset properties can be updated at runtime."""
        controller_no_boost.heating_boost_offset = 3.0
        controller_no_boost.cooling_boost_offset = 2.5

        assert controller_no_boost.heating_boost_offset == 3.0
        assert controller_no_boost.cooling_boost_offset == 2.5

    @pytest.mark.asyncio
    async def test_heat_mode_applies_boost(self, controller_with_heating_boost, mock_hass):
        """Test that heating boost is applied when turning on in heat mode."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 70.0
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs heating"

        controller_with_heating_boost._previous_hvac_mode = HVACMode.HEAT.value

        await controller_with_heating_boost.async_execute_action(thermostat_state)

        # Find the set_temperature call
        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 1
        call_data = temp_calls[0][0][2]
        assert call_data["entity_id"] == TEST_THERMOSTAT
        # Should be target (70) + boost (2) = 72
        assert call_data["temperature"] == 72.0

    @pytest.mark.asyncio
    async def test_cool_mode_applies_boost(self, controller_with_cooling_boost, mock_hass):
        """Test that cooling boost is applied when turning on in cool mode."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 75.0
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs cooling"

        controller_with_cooling_boost._previous_hvac_mode = HVACMode.COOL.value

        await controller_with_cooling_boost.async_execute_action(thermostat_state)

        # Find the set_temperature call
        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 1
        call_data = temp_calls[0][0][2]
        assert call_data["entity_id"] == TEST_THERMOSTAT
        # Should be target (75) - boost (2) = 73
        assert call_data["temperature"] == 73.0

    @pytest.mark.asyncio
    async def test_heat_cool_mode_applies_both_boosts(
        self, controller_with_both_boosts, mock_hass
    ):
        """Test that both boosts are applied in heat_cool mode."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temp_low = 68.0
        thermostat_state.target_temp_high = 76.0
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs conditioning"

        controller_with_both_boosts._previous_hvac_mode = HVACMode.HEAT_COOL.value

        await controller_with_both_boosts.async_execute_action(thermostat_state)

        # Find the set_temperature call
        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 1
        call_data = temp_calls[0][0][2]
        assert call_data["entity_id"] == TEST_THERMOSTAT
        # Low should be target_low (68) + heat_boost (2) = 70
        assert call_data["target_temp_low"] == 70.0
        # High should be target_high (76) - cool_boost (1.5) = 74.5
        assert call_data["target_temp_high"] == 74.5

    @pytest.mark.asyncio
    async def test_no_boost_still_sets_temperature(self, controller_no_boost, mock_hass):
        """Test that temperature is still set even when boost is 0.

        This is critical for away mode - even without boost, we need to
        sync the effective temperature to the physical thermostat.
        """
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 67.0  # Away-adjusted temp
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs heating"

        controller_no_boost._previous_hvac_mode = HVACMode.HEAT.value

        await controller_no_boost.async_execute_action(thermostat_state)

        # Find the set_temperature call
        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 1
        call_data = temp_calls[0][0][2]
        assert call_data["entity_id"] == TEST_THERMOSTAT
        # Should be exactly the target temp (no boost)
        assert call_data["temperature"] == 67.0

    @pytest.mark.asyncio
    async def test_boost_with_away_mode_temps(self, controller_with_heating_boost, mock_hass):
        """Test that boost is correctly applied on top of away-adjusted temps.

        Scenario: Home target is 70°F, away adjustment is -3°F, boost is +2°F
        Expected: 70 - 3 + 2 = 69°F sent to thermostat
        """
        mock_hass.states.get.return_value = None

        # The thermostat_state already contains away-adjusted temps
        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 67.0  # Already away-adjusted (70 - 3)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs heating"

        controller_with_heating_boost._previous_hvac_mode = HVACMode.HEAT.value

        await controller_with_heating_boost.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 1
        call_data = temp_calls[0][0][2]
        # Should be away-adjusted (67) + boost (2) = 69
        assert call_data["temperature"] == 69.0

    @pytest.mark.asyncio
    async def test_turn_off_does_not_set_temperature(
        self, controller_with_heating_boost, mock_hass
    ):
        """Test that TURN_OFF action does not set temperature."""
        mock_state = MagicMock()
        mock_state.state = HVACMode.HEAT
        mock_hass.states.get.return_value = mock_state

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 70.0
        thermostat_state.recommended_action = ThermostatAction.TURN_OFF
        thermostat_state.action_reason = "All rooms satiated"

        await controller_with_heating_boost.async_execute_action(thermostat_state)

        # Should call set_hvac_mode but NOT set_temperature
        calls = mock_hass.services.async_call.call_args_list
        hvac_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(hvac_calls) >= 1  # Should set HVAC mode to off
        assert len(temp_calls) == 0  # Should NOT set temperature

    @pytest.mark.asyncio
    async def test_wait_actions_do_not_set_temperature(
        self, controller_with_heating_boost, mock_hass
    ):
        """Test that WAIT actions do not set temperature."""
        for action in [ThermostatAction.WAIT_CYCLE_ON, ThermostatAction.WAIT_CYCLE_OFF]:
            mock_hass.services.async_call.reset_mock()

            thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
            thermostat_state.target_temperature = 70.0
            thermostat_state.recommended_action = action
            thermostat_state.action_reason = "Waiting for cycle protection"

            await controller_with_heating_boost.async_execute_action(thermostat_state)

            # Should not call any services during wait
            calls = mock_hass.services.async_call.call_args_list
            assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_none_action_does_not_set_temperature(
        self, controller_with_heating_boost, mock_hass
    ):
        """Test that NONE action does not set temperature."""
        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = 70.0
        thermostat_state.recommended_action = ThermostatAction.NONE
        thermostat_state.action_reason = "No action needed"

        await controller_with_heating_boost.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_missing_target_temp_skips_temperature_set(
        self, controller_with_heating_boost, mock_hass
    ):
        """Test that missing target temperature skips the set_temperature call."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temperature = None  # No target temp
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.action_reason = "Room needs heating"

        controller_with_heating_boost._previous_hvac_mode = HVACMode.HEAT.value

        await controller_with_heating_boost.async_execute_action(thermostat_state)

        # Should still call set_hvac_mode but skip set_temperature
        calls = mock_hass.services.async_call.call_args_list
        hvac_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(hvac_calls) == 1
        assert len(temp_calls) == 0

    @pytest.mark.asyncio
    async def test_heat_cool_missing_both_temps_skips_set(
        self, controller_with_both_boosts, mock_hass
    ):
        """Test heat_cool mode with missing temps skips set_temperature."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.target_temp_low = None
        thermostat_state.target_temp_high = None
        thermostat_state.recommended_action = ThermostatAction.TURN_ON

        controller_with_both_boosts._previous_hvac_mode = HVACMode.HEAT_COOL.value

        await controller_with_both_boosts.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        temp_calls = [c for c in calls if c[0][1] == "set_temperature"]

        assert len(temp_calls) == 0

    def test_default_boost_values_are_zero(self, mock_hass, mock_occupancy_tracker):
        """Test that default boost values are zero when not specified."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )

        assert controller.heating_boost_offset == 0.0
        assert controller.cooling_boost_offset == 0.0


# =============================================================================
# Tests for infer_effective_hvac_mode
# =============================================================================


class TestInferEffectiveHvacMode:
    """Tests for the infer_effective_hvac_mode function."""

    def test_closer_to_heat_below_target(self):
        """Test that mode is HEAT when avg temp is below heating target."""
        readings = {
            "sensor.living_room": 68.0,
            "sensor.bedroom": 66.0,
            "sensor.kitchen": 67.0,
        }
        # Avg = 67°F, target_low = 71, target_high = 78
        # Distance to heat = 71 - 67 = 4 (positive, below target)
        # Distance to cool = 67 - 78 = -11 (negative, not above)
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.HEAT

    def test_closer_to_cool_above_target(self):
        """Test that mode is COOL when avg temp is above cooling target."""
        readings = {
            "sensor.living_room": 80.0,
            "sensor.bedroom": 82.0,
            "sensor.kitchen": 81.0,
        }
        # Avg = 81°F, target_low = 71, target_high = 78
        # Distance to heat = 71 - 81 = -10 (negative, above target)
        # Distance to cool = 81 - 78 = 3 (positive, above target)
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.COOL

    def test_in_comfort_band_closer_to_heat(self):
        """Test mode is HEAT when in comfort band but closer to heat threshold."""
        readings = {
            "sensor.living_room": 72.0,
            "sensor.bedroom": 73.0,
        }
        # Avg = 72.5°F, target_low = 71, target_high = 78
        # Distance to heat = 71 - 72.5 = -1.5 (1.5 above heat target)
        # Distance to cool = 72.5 - 78 = -5.5 (5.5 below cool target)
        # Closer to heat threshold (1.5 vs 5.5)
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.HEAT

    def test_in_comfort_band_closer_to_cool(self):
        """Test mode is COOL when in comfort band but closer to cool threshold."""
        readings = {
            "sensor.living_room": 76.0,
            "sensor.bedroom": 77.0,
        }
        # Avg = 76.5°F, target_low = 71, target_high = 78
        # Distance to heat = 71 - 76.5 = -5.5 (5.5 above heat target)
        # Distance to cool = 76.5 - 78 = -1.5 (1.5 below cool target)
        # Closer to cool threshold (1.5 vs 5.5)
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.COOL

    def test_exactly_at_heat_threshold(self):
        """Test mode is HEAT when exactly at heat threshold."""
        readings = {"sensor.temp": 71.0}
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.HEAT

    def test_exactly_at_cool_threshold(self):
        """Test mode is COOL when exactly at cool threshold."""
        readings = {"sensor.temp": 78.0}
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.COOL

    def test_exactly_at_midpoint(self):
        """Test mode when exactly at midpoint of comfort band."""
        readings = {"sensor.temp": 74.5}  # Midpoint of 71-78
        # Distance to heat = 71 - 74.5 = -3.5
        # Distance to cool = 74.5 - 78 = -3.5
        # Equal distance - should pick one consistently
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        # When equal, abs comparison: abs(-3.5) < abs(-3.5) is False, so COOL
        assert result == HVACMode.COOL

    def test_no_readings_returns_none(self):
        """Test that empty readings returns None."""
        result = infer_effective_hvac_mode({}, 71.0, 78.0)
        assert result is None

    def test_no_target_low_returns_none(self):
        """Test that missing target_low returns None."""
        readings = {"sensor.temp": 72.0}
        result = infer_effective_hvac_mode(readings, None, 78.0)
        assert result is None

    def test_no_target_high_returns_none(self):
        """Test that missing target_high returns None."""
        readings = {"sensor.temp": 72.0}
        result = infer_effective_hvac_mode(readings, 71.0, None)
        assert result is None

    def test_single_sensor(self):
        """Test inference with only one sensor."""
        readings = {"sensor.only": 68.0}
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.HEAT

    def test_many_sensors_averaged(self):
        """Test that all sensors are averaged correctly."""
        readings = {
            "sensor.1": 70.0,
            "sensor.2": 70.0,
            "sensor.3": 70.0,
            "sensor.4": 70.0,
            "sensor.5": 80.0,  # One hot room
        }
        # Avg = (70*4 + 80) / 5 = 72°F
        result = infer_effective_hvac_mode(readings, 71.0, 78.0)
        assert result == HVACMode.HEAT  # 72 is closer to 71 than 78

    def test_away_mode_adjusted_targets(self):
        """Test with away mode adjusted targets (wider band)."""
        readings = {"sensor.temp": 66.0}
        # Away targets: heat=68 (71-3), cool=81 (78+3)
        result = infer_effective_hvac_mode(readings, 68.0, 81.0)
        assert result == HVACMode.HEAT  # 66 is below 68 heat target


# =============================================================================
# Tests for determine_rooms_need_mode
# =============================================================================


class TestDetermineRoomsNeedMode:
    """Tests for the determine_rooms_need_mode function."""

    def test_active_room_needs_heat(self):
        """Test that active room below heat threshold needs heat."""
        room = RoomTemperatureState(
            area_id="living_room",
            area_name="Living Room",
            is_active=True,
            is_satiated=False,
            determining_temperature=68.0,  # Below 70.5 (71 - 0.5)
        )
        room_states = {"living_room": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is True
        assert need_cool is False

    def test_active_room_needs_cool(self):
        """Test that active room above cool threshold needs cool."""
        room = RoomTemperatureState(
            area_id="living_room",
            area_name="Living Room",
            is_active=True,
            is_satiated=False,
            determining_temperature=80.0,  # Above 78.5 (78 + 0.5)
        )
        room_states = {"living_room": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is True

    def test_critical_room_needs_heat(self):
        """Test that critical room below heat threshold needs heat."""
        room = RoomTemperatureState(
            area_id="basement",
            area_name="Basement",
            is_satiated=True,  # Satiated but critical
            is_critical=True,
            determining_temperature=62.0,
        )
        room_states = {"basement": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is True
        assert need_cool is False

    def test_critical_room_needs_cool(self):
        """Test that critical room above cool threshold needs cool."""
        room = RoomTemperatureState(
            area_id="attic",
            area_name="Attic",
            is_satiated=True,
            is_critical=True,
            determining_temperature=85.0,
        )
        room_states = {"attic": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is True

    def test_inactive_room_in_comfort_zone_ignored(self):
        """Test that inactive rooms in comfort zone don't affect needs.
        
        An inactive room at 69°F is below the comfort threshold for heating
        (70.5°F) but above the critical threshold (68°F = 71 - 3).
        Since it's not in critical range, it shouldn't trigger need_heat.
        """
        room = RoomTemperatureState(
            area_id="living_room",
            area_name="Living Room",
            is_satiated=True,
            is_critical=False,
            determining_temperature=69.0,  # Cold but above critical (68°F)
        )
        room_states = {"living_room": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is False

    def test_inactive_room_in_critical_heat_range_needs_heat(self):
        """Test that inactive room in critical heat range triggers need_heat.
        
        An inactive room at 65°F is below the critical heating threshold
        (68°F = 71 - 3), so it should trigger need_heat even though it's
        inactive and "satiated".
        """
        room = RoomTemperatureState(
            area_id="basement",
            area_name="Basement",
            is_satiated=True,  # Mode-specific satiation
            is_critical=False,  # is_critical might not be set if eval'd in wrong mode
            determining_temperature=65.0,  # Below critical threshold (68°F)
        )
        room_states = {"basement": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is True
        assert need_cool is False

    def test_inactive_room_in_critical_cool_range_needs_cool(self):
        """Test that inactive room in critical cool range triggers need_cool.
        
        An inactive room at 83°F is above the critical cooling threshold
        (81°F = 78 + 3), so it should trigger need_cool even though it's
        inactive.
        """
        room = RoomTemperatureState(
            area_id="attic",
            area_name="Attic",
            is_satiated=True,
            is_critical=False,  # is_critical might not be set if eval'd in wrong mode
            determining_temperature=83.0,  # Above critical threshold (81°F)
        )
        room_states = {"attic": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is True

    def test_mixed_active_rooms_some_need_heat_some_cool(self):
        """Test with active rooms needing both heat and cool."""
        room1 = RoomTemperatureState(
            area_id="theater",
            area_name="Theater",
            is_active=True,
            is_satiated=False,
            determining_temperature=68.0,
        )
        room2 = RoomTemperatureState(
            area_id="kitchen",
            area_name="Kitchen",
            is_active=True,
            is_satiated=False,
            determining_temperature=80.0,
        )
        room_states = {"theater": room1, "kitchen": room2}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is True
        assert need_cool is True

    def test_active_room_in_comfort_band_no_needs(self):
        """Test active room in comfort band doesn't need heat or cool."""
        room = RoomTemperatureState(
            area_id="living_room",
            area_name="Living Room",
            is_active=True,
            is_satiated=False,
            determining_temperature=74.0,  # In comfort band
        )
        room_states = {"living_room": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is False

    def test_empty_room_states(self):
        """Test with no rooms."""
        need_heat, need_cool = determine_rooms_need_mode({}, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is False

    def test_room_without_temperature(self):
        """Test active room without determining temperature."""
        room = RoomTemperatureState(
            area_id="living_room",
            area_name="Living Room",
            is_active=True,
            is_satiated=False,
            determining_temperature=None,
        )
        room_states = {"living_room": room}
        need_heat, need_cool = determine_rooms_need_mode(room_states, 71.0, 78.0, 0.5)
        assert need_heat is False
        assert need_cool is False


# =============================================================================
# Tests for Consensus-Based HVAC Mode Selection
# =============================================================================


class TestConsensusBasedHvacModeSelection:
    """Tests for the consensus logic in evaluate_thermostat_action."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self, mock_hass):
        """Create mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        tracker.hass = mock_hass
        return tracker

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create thermostat controller for testing."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
            temperature_deadband=0.5,
        )
        return controller

    def _create_thermostat_state(self, mock_hass, hvac_mode=HVACMode.OFF, 
                                   target_low=71.0, target_high=78.0):
        """Helper to create thermostat state mock."""
        mock_state = MagicMock()
        mock_state.state = hvac_mode.value if hvac_mode else STATE_OFF
        mock_state.attributes = {
            ATTR_TARGET_TEMP_LOW: target_low,
            ATTR_TARGET_TEMP_HIGH: target_high,
            ATTR_TEMPERATURE: target_low if hvac_mode == HVACMode.HEAT else target_high,
        }
        return mock_state

    def _create_temp_sensor_state(self, temp):
        """Helper to create temperature sensor state mock."""
        mock_state = MagicMock()
        mock_state.state = str(temp)
        return mock_state

    # =========================================================================
    # Category 1: Aligned Scenarios - Should Engage
    # =========================================================================

    def test_cold_house_active_room_needs_heat_engages_heat(
        self, controller, mock_hass
    ):
        """Scenario 1A: Cold house, active room needs heat -> ENGAGE HEAT."""
        # Setup thermostat as OFF
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(67.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(66.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, [], respect_user_off=False
        )

        assert result.recommended_action == ThermostatAction.TURN_ON
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.rooms_need_heat is True
        assert "Trend=HEAT" in result.action_reason

    def test_hot_house_active_room_needs_cool_engages_cool(
        self, controller, mock_hass
    ):
        """Scenario 1B: Hot house, active room needs cool -> ENGAGE COOL."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.office_temp": self._create_temp_sensor_state(82.0),
            "sensor.living_room_temp": self._create_temp_sensor_state(80.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="office", area_name="Office"),
        ]
        area_temp_sensors = {
            "office": ["sensor.office_temp"],
            "living_room": ["sensor.living_room_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, 
            [AreaOccupancyState(area_id="living_room", area_name="Living Room")],
            respect_user_off=False
        )

        assert result.recommended_action == ThermostatAction.TURN_ON
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_cool is True
        assert "Trend=COOL" in result.action_reason

    def test_shoulder_season_cold_trend_room_needs_heat_engages_heat(
        self, controller, mock_hass
    ):
        """Scenario 1C: Shoulder season, trend=HEAT, room needs heat -> ENGAGE."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.theater_temp": self._create_temp_sensor_state(68.0),  # Needs heat
            "sensor.office_temp": self._create_temp_sensor_state(73.0),  # Satiated
            "sensor.living_room_temp": self._create_temp_sensor_state(70.0),  # Pulls avg down
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="theater", area_name="Theater"),
            AreaOccupancyState(area_id="office", area_name="Office"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        area_temp_sensors = {
            "theater": ["sensor.theater_temp"],
            "office": ["sensor.office_temp"],
            "living_room": ["sensor.living_room_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (68 + 73 + 70) / 3 = 70.33, closer to 71 than 78 -> HEAT
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.recommended_action == ThermostatAction.TURN_ON

    # =========================================================================
    # Category 2: Anomaly Scenarios - Should NOT Engage
    # =========================================================================

    def test_cold_house_but_hot_kitchen_anomaly(self, controller, mock_hass):
        """Scenario 2A: Cold house, but active kitchen is hot -> ANOMALY."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.kitchen_temp": self._create_temp_sensor_state(80.0),  # Hot kitchen
            "sensor.living_room_temp": self._create_temp_sensor_state(69.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(68.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        area_temp_sensors = {
            "kitchen": ["sensor.kitchen_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors,
            [AreaOccupancyState(area_id="bedroom", area_name="Bedroom")],
            respect_user_off=False
        )

        # Avg = (80 + 69 + 68) / 3 = 72.33, closer to 71 -> HEAT
        # But kitchen at 80 needs COOL -> Mismatch
        # Living room at 69 is actually satiated for heat (69 > 70.5? No, 69 < 70.5)
        # So living room needs heat, kitchen needs cool
        # Trend=HEAT, rooms_need_heat=True -> Should engage HEAT
        assert result.inferred_hvac_mode == HVACMode.HEAT
        # Since at least one room (living room) needs heat and trend=HEAT, it should engage
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_hot_house_but_cold_basement_anomaly(self, controller, mock_hass):
        """Scenario 2B: Hot house, but basement needs heat -> ANOMALY if only basement."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.basement_temp": self._create_temp_sensor_state(65.0),  # Cold basement
            "sensor.office_temp": self._create_temp_sensor_state(77.0),  # Satiated
            "sensor.living_room_temp": self._create_temp_sensor_state(80.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(79.0),
        }.get(entity_id)

        # Only basement is active, it's cold
        active_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="office", area_name="Office"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "basement": ["sensor.basement_temp"],
            "office": ["sensor.office_temp"],
            "living_room": ["sensor.living_room_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (65 + 77 + 80 + 79) / 4 = 75.25, closer to 78 than 71 -> COOL
        # Basement needs HEAT, but trend=COOL -> ANOMALY
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_heat is True
        assert result.rooms_need_cool is False
        assert result.recommended_action == ThermostatAction.NONE
        assert "Anomaly" in result.action_reason

    def test_warm_house_cold_theater_anomaly(self, controller, mock_hass):
        """Scenario 2C: Warm house, cold theater -> ANOMALY."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.theater_temp": self._create_temp_sensor_state(68.0),  # Cold
            "sensor.living_room_temp": self._create_temp_sensor_state(76.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(77.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(78.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="theater", area_name="Theater"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "theater": ["sensor.theater_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (68 + 76 + 77 + 78) / 4 = 74.75, closer to cool (78) than heat (71)
        # Theater needs HEAT, trend=COOL -> ANOMALY
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.recommended_action == ThermostatAction.NONE
        assert "Anomaly" in result.action_reason

    # =========================================================================
    # Category 3: All Satiated - No Action
    # =========================================================================

    def test_all_active_rooms_satiated_no_action(self, controller, mock_hass):
        """Scenario 3A: All active rooms satiated -> No action."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(73.0),
            "sensor.office_temp": self._create_temp_sensor_state(75.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="office", area_name="Office"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "office": ["sensor.office_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, [], respect_user_off=False
        )

        assert result.recommended_action == ThermostatAction.NONE
        assert result.all_active_rooms_satiated is True
        assert "Already off, all rooms satiated" in result.action_reason

    # =========================================================================
    # Category 4: Critical Room Scenarios
    # =========================================================================

    def test_critical_room_aligned_with_trend_engages(self, controller, mock_hass):
        """Scenario 2A revised: Critical basement, trend=HEAT -> ENGAGE."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.basement_temp": self._create_temp_sensor_state(62.0),  # Critical cold
            "sensor.living_room_temp": self._create_temp_sensor_state(68.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(69.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0  # Critical below 68°F

        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "basement": ["sensor.basement_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (62 + 68 + 69) / 3 = 66.33 -> HEAT
        # Basement is critical (62 < 68), needs heat
        # Trend=HEAT, critical needs HEAT -> ENGAGE
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.critical_room_count >= 1
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_critical_room_misaligned_with_trend_anomaly(self, controller, mock_hass):
        """Scenario: Critical basement needs heat, but house trend=COOL -> ANOMALY.
        
        The basement is critically cold (60°F, which is below 71-3=68 threshold),
        but the rest of the house is warm enough that the trend is COOL.
        None of the warm rooms are critical for cooling (all < 81°F threshold).
        """
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.basement_temp": self._create_temp_sensor_state(60.0),  # Critical cold (< 68)
            "sensor.living_room_temp": self._create_temp_sensor_state(80.0),  # Warm but not critical (< 81)
            "sensor.bedroom_temp": self._create_temp_sensor_state(80.0),  # Warm but not critical (< 81)
            "sensor.kitchen_temp": self._create_temp_sensor_state(80.0),  # Warm but not critical (< 81)
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0  # Critical below 68°F
        controller._unoccupied_cooling_threshold = 3.0  # Critical above 81°F

        active_areas = []  # No active areas
        inactive_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "basement": ["sensor.basement_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (60 + 80 + 80 + 80) / 4 = 75 -> COOL (75 is closer to 78 than 71)
        # Basement is critical and needs HEAT, but trend=COOL -> ANOMALY
        # Other rooms are warm but NOT critical (80 < 81 threshold)
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_heat is True
        assert result.rooms_need_cool is False  # No rooms are critical for cooling
        assert result.recommended_action == ThermostatAction.NONE
        assert "Anomaly" in result.action_reason

    # =========================================================================
    # Category 5: Everyone Away (No Active Rooms) Scenarios  
    # =========================================================================

    def test_everyone_away_all_rooms_comfortable_stays_off(self, controller, mock_hass):
        """Everyone away, all rooms comfortable -> stay OFF."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(73.0),  # Comfortable
            "sensor.bedroom_temp": self._create_temp_sensor_state(74.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(72.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = []  # Everyone away
        inactive_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # All rooms between 68°F (heat critical) and 81°F (cool critical)
        # No active rooms = should stay off
        assert result.recommended_action == ThermostatAction.NONE
        assert result.rooms_need_heat is False
        assert result.rooms_need_cool is False

    def test_everyone_away_one_room_critical_cold_turns_on(self, controller, mock_hass):
        """Everyone away, one room critically cold -> turn ON for heat."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.basement_temp": self._create_temp_sensor_state(55.0),  # Critically cold
            "sensor.living_room_temp": self._create_temp_sensor_state(70.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(69.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = []  # Everyone away
        inactive_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "basement": ["sensor.basement_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Basement at 55°F is critically cold (< 68°F)
        # Avg = (55 + 70 + 69) / 3 = 64.67 -> trend=HEAT
        # Critical cold room + trend=HEAT = consensus, turn ON
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.rooms_need_heat is True
        assert result.critical_room_count >= 1
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_everyone_away_one_room_critical_hot_turns_on(self, controller, mock_hass):
        """Everyone away, one room critically hot -> turn ON for cool."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.attic_temp": self._create_temp_sensor_state(90.0),  # Critically hot
            "sensor.living_room_temp": self._create_temp_sensor_state(78.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(79.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = []  # Everyone away
        inactive_areas = [
            AreaOccupancyState(area_id="attic", area_name="Attic"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "attic": ["sensor.attic_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Attic at 90°F is critically hot (> 81°F)
        # Avg = (90 + 78 + 79) / 3 = 82.33 -> trend=COOL
        # Critical hot room + trend=COOL = consensus, turn ON
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_cool is True
        assert result.critical_room_count >= 1
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_everyone_away_hvac_already_running_keeps_running_if_needed(
        self, controller, mock_hass
    ):
        """Everyone away, HVAC running, critical room exists -> keep running."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.HEAT),
            "sensor.basement_temp": self._create_temp_sensor_state(60.0),  # Still cold
            "sensor.living_room_temp": self._create_temp_sensor_state(70.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(71.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = []  # Everyone away
        inactive_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "basement": ["sensor.basement_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Basement is still critical, HVAC already heating
        # Should stay on (NONE = no change needed)
        assert result.critical_room_count >= 1
        assert result.recommended_action == ThermostatAction.NONE

    def test_everyone_away_hvac_running_all_satiated_turns_off(
        self, controller, mock_hass
    ):
        """Everyone away, HVAC running, all rooms now comfortable -> turn off."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.HEAT),
            "sensor.basement_temp": self._create_temp_sensor_state(72.0),  # Warmed up
            "sensor.living_room_temp": self._create_temp_sensor_state(73.0),
            "sensor.bedroom_temp": self._create_temp_sensor_state(72.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = []  # Everyone away
        inactive_areas = [
            AreaOccupancyState(area_id="basement", area_name="Basement"),
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
        ]
        area_temp_sensors = {
            "basement": ["sensor.basement_temp"],
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # All rooms now comfortable, no active rooms = should turn off
        assert result.critical_room_count == 0
        assert result.recommended_action == ThermostatAction.TURN_OFF

    # =========================================================================
    # Category 6: Mixed Active/Inactive Scenarios
    # =========================================================================

    def test_mixed_rooms_trend_heat_engages_heat(self, controller, mock_hass):
        """Scenario 4C: Mixed rooms, trend=HEAT, one needs heat -> ENGAGE HEAT."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.theater_temp": self._create_temp_sensor_state(68.0),  # Needs heat
            "sensor.kitchen_temp": self._create_temp_sensor_state(80.0),  # Needs cool
            "sensor.living_room_temp": self._create_temp_sensor_state(71.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="theater", area_name="Theater"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "theater": ["sensor.theater_temp"],
            "kitchen": ["sensor.kitchen_temp"],
            "living_room": ["sensor.living_room_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors,
            [AreaOccupancyState(area_id="living_room", area_name="Living Room")],
            respect_user_off=False
        )

        # Avg = (68 + 80 + 71) / 3 = 73 -> HEAT (closer to 71)
        # Theater needs HEAT, kitchen needs COOL
        # Trend=HEAT, one room needs HEAT -> ENGAGE HEAT
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.rooms_need_heat is True
        assert result.rooms_need_cool is True
        assert result.recommended_action == ThermostatAction.TURN_ON
        assert "Trend=HEAT" in result.action_reason

    def test_mixed_rooms_trend_cool_engages_cool(self, controller, mock_hass):
        """Scenario 4D: Mixed rooms, trend=COOL, one needs cool -> ENGAGE COOL."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.theater_temp": self._create_temp_sensor_state(68.0),  # Needs heat
            "sensor.kitchen_temp": self._create_temp_sensor_state(80.0),  # Needs cool
            "sensor.living_room_temp": self._create_temp_sensor_state(79.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="theater", area_name="Theater"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "theater": ["sensor.theater_temp"],
            "kitchen": ["sensor.kitchen_temp"],
            "living_room": ["sensor.living_room_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors,
            [AreaOccupancyState(area_id="living_room", area_name="Living Room")],
            respect_user_off=False
        )

        # Avg = (68 + 80 + 79) / 3 = 75.67 -> COOL (closer to 78)
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.recommended_action == ThermostatAction.TURN_ON
        assert "Trend=COOL" in result.action_reason

    # =========================================================================
    # Category 7: Returning Home (Transition from Away to Active)
    # =========================================================================

    def test_returning_home_to_cold_room_turns_on_heat(self, controller, mock_hass):
        """User returns home to a cold room -> turn on heat."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(66.0),  # Cold
            "sensor.bedroom_temp": self._create_temp_sensor_state(67.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(68.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        # User just came home to living room
        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Living room at 66°F is below comfort threshold (70.5°F)
        # Avg = 67°F -> trend=HEAT
        # Active room needs heat + trend=HEAT = consensus, turn ON
        assert result.inferred_hvac_mode == HVACMode.HEAT
        assert result.rooms_need_heat is True
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_returning_home_to_hot_room_turns_on_cool(self, controller, mock_hass):
        """User returns home to a hot room -> turn on cooling."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(82.0),  # Hot
            "sensor.bedroom_temp": self._create_temp_sensor_state(80.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(81.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        # User just came home to living room
        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Living room at 82°F is above comfort threshold (78.5°F)
        # Avg = 81°F -> trend=COOL
        # Active room needs cool + trend=COOL = consensus, turn ON
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_cool is True
        assert result.recommended_action == ThermostatAction.TURN_ON

    def test_returning_home_room_comfortable_stays_off(self, controller, mock_hass):
        """User returns home to comfortable room -> stay off."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(73.0),  # Comfortable
            "sensor.bedroom_temp": self._create_temp_sensor_state(74.0),
            "sensor.kitchen_temp": self._create_temp_sensor_state(72.0),
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Room is comfortable, no need for HVAC
        assert result.recommended_action == ThermostatAction.NONE
        assert result.rooms_need_heat is False
        assert result.rooms_need_cool is False

    def test_returning_home_cold_room_but_hot_house_anomaly(self, controller, mock_hass):
        """User returns home to cold room, but rest of house is hot -> anomaly.
        
        This tests the scenario where one room has AC vent blowing on it or
        a drafty window, making it cold while rest of house is warm.
        """
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
            "sensor.living_room_temp": self._create_temp_sensor_state(67.0),  # Cold (drafty)
            "sensor.bedroom_temp": self._create_temp_sensor_state(80.0),  # Hot
            "sensor.kitchen_temp": self._create_temp_sensor_state(79.0),  # Hot
        }.get(entity_id)

        controller._unoccupied_heating_threshold = 3.0
        controller._unoccupied_cooling_threshold = 3.0

        active_areas = [
            AreaOccupancyState(area_id="living_room", area_name="Living Room"),
        ]
        inactive_areas = [
            AreaOccupancyState(area_id="bedroom", area_name="Bedroom"),
            AreaOccupancyState(area_id="kitchen", area_name="Kitchen"),
        ]
        area_temp_sensors = {
            "living_room": ["sensor.living_room_temp"],
            "bedroom": ["sensor.bedroom_temp"],
            "kitchen": ["sensor.kitchen_temp"],
        }

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, inactive_areas, respect_user_off=False
        )

        # Avg = (67 + 80 + 79) / 3 = 75.33 -> trend=COOL
        # Active room (living room) needs HEAT, but trend=COOL -> ANOMALY
        assert result.inferred_hvac_mode == HVACMode.COOL
        assert result.rooms_need_heat is True  # Active room is cold
        assert result.recommended_action == ThermostatAction.NONE
        assert "Anomaly" in result.action_reason

    # =========================================================================
    # Category 8: Edge Cases
    # =========================================================================

    def test_no_sensors_no_inference(self, controller, mock_hass):
        """Scenario 4E: No temperature sensors -> Can't infer mode."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.OFF),
        }.get(entity_id)

        active_areas = []
        area_temp_sensors = {}

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, [], respect_user_off=False
        )

        # No active rooms, no sensors -> no action
        assert result.recommended_action == ThermostatAction.NONE

    def test_thermostat_already_on_no_mode_change(self, controller, mock_hass):
        """Scenario 4G: Thermostat already on -> No action needed."""
        mock_hass.states.get.side_effect = lambda entity_id: {
            TEST_THERMOSTAT: self._create_thermostat_state(mock_hass, HVACMode.HEAT),
            "sensor.theater_temp": self._create_temp_sensor_state(67.0),
        }.get(entity_id)

        active_areas = [
            AreaOccupancyState(area_id="theater", area_name="Theater"),
        ]
        area_temp_sensors = {"theater": ["sensor.theater_temp"]}

        result = controller.evaluate_thermostat_action(
            active_areas, area_temp_sensors, [], respect_user_off=False
        )

        # Already on, just continue
        assert result.recommended_action == ThermostatAction.NONE
        assert "Already on" in result.action_reason


# =============================================================================
# Tests for Execute Action with Inferred Mode
# =============================================================================


class TestExecuteActionWithInferredMode:
    """Tests for async_execute_action using inferred HVAC mode."""

    @pytest.fixture
    def mock_hass(self):
        """Create mock Home Assistant instance."""
        hass = MagicMock(spec=HomeAssistant)
        hass.states = MagicMock()
        hass.services = MagicMock()
        hass.services.async_call = AsyncMock()
        return hass

    @pytest.fixture
    def mock_occupancy_tracker(self, mock_hass):
        """Create mock occupancy tracker."""
        tracker = MagicMock(spec=RoomOccupancyTracker)
        tracker.hass = mock_hass
        return tracker

    @pytest.fixture
    def controller(self, mock_hass, mock_occupancy_tracker):
        """Create thermostat controller for testing."""
        controller = ThermostatController(
            hass=mock_hass,
            thermostat_entity_id=TEST_THERMOSTAT,
            occupancy_tracker=mock_occupancy_tracker,
        )
        return controller

    @pytest.mark.asyncio
    async def test_execute_uses_inferred_heat_mode(self, controller, mock_hass):
        """Test that execute action uses inferred HEAT mode."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.inferred_hvac_mode = HVACMode.HEAT
        thermostat_state.action_reason = "Rooms need heat"
        thermostat_state.target_temperature = 71.0

        await controller.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        hvac_mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        
        assert len(hvac_mode_calls) == 1
        assert hvac_mode_calls[0][0][2]["hvac_mode"] == "heat"

    @pytest.mark.asyncio
    async def test_execute_uses_inferred_cool_mode(self, controller, mock_hass):
        """Test that execute action uses inferred COOL mode."""
        mock_hass.states.get.return_value = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.inferred_hvac_mode = HVACMode.COOL
        thermostat_state.action_reason = "Rooms need cool"
        thermostat_state.target_temperature = 78.0

        await controller.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        hvac_mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        
        assert len(hvac_mode_calls) == 1
        assert hvac_mode_calls[0][0][2]["hvac_mode"] == "cool"

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_previous_mode(self, controller, mock_hass):
        """Test fallback to previous mode when no inferred mode."""
        mock_hass.states.get.return_value = None
        controller._previous_hvac_mode = HVACMode.COOL.value

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.inferred_hvac_mode = None  # No inferred mode
        thermostat_state.action_reason = "Rooms need conditioning"

        await controller.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        hvac_mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        
        assert len(hvac_mode_calls) == 1
        assert hvac_mode_calls[0][0][2]["hvac_mode"] == "cool"

    @pytest.mark.asyncio
    async def test_execute_defaults_to_heat_when_nothing_available(
        self, controller, mock_hass
    ):
        """Test default to HEAT when no inferred or previous mode."""
        mock_hass.states.get.return_value = None
        controller._previous_hvac_mode = None

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.inferred_hvac_mode = None
        thermostat_state.action_reason = "Rooms need conditioning"

        await controller.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        hvac_mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        
        assert len(hvac_mode_calls) == 1
        assert hvac_mode_calls[0][0][2]["hvac_mode"] == "heat"

    @pytest.mark.asyncio
    async def test_inferred_mode_takes_precedence_over_previous(
        self, controller, mock_hass
    ):
        """Test that inferred mode takes precedence over previous mode."""
        mock_hass.states.get.return_value = None
        controller._previous_hvac_mode = HVACMode.HEAT.value  # Previous was heat

        thermostat_state = ThermostatState(thermostat_entity_id=TEST_THERMOSTAT)
        thermostat_state.recommended_action = ThermostatAction.TURN_ON
        thermostat_state.inferred_hvac_mode = HVACMode.COOL  # But inferred is cool
        thermostat_state.action_reason = "Rooms need cool"
        thermostat_state.target_temperature = 78.0

        await controller.async_execute_action(thermostat_state)

        calls = mock_hass.services.async_call.call_args_list
        hvac_mode_calls = [c for c in calls if c[0][1] == "set_hvac_mode"]
        
        assert len(hvac_mode_calls) == 1
        # Inferred mode (COOL) should win over previous (HEAT)
        assert hvac_mode_calls[0][0][2]["hvac_mode"] == "cool"
