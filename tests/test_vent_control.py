"""Tests for the vent control module."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OPEN,
    STATE_CLOSED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)

from custom_components.thermostat_contact_sensors.vent_control import (
    VentController,
    VentState,
    AreaVentState,
    VentControlState,
    SERVICE_OPEN_COVER_TILT,
    SERVICE_CLOSE_COVER_TILT,
)
from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState
from custom_components.thermostat_contact_sensors.thermostat_control import (
    RoomTemperatureState,
)

# Test constants
TEST_VENT_1 = "cover.bedroom_vent"
TEST_VENT_2 = "cover.living_room_vent"
TEST_VENT_3 = "cover.office_vent"
TEST_VENT_GROUP = "cover.all_hallway_vents"
TEST_AREA_BEDROOM = "bedroom"
TEST_AREA_LIVING_ROOM = "living_room"
TEST_AREA_OFFICE = "office"
TEST_AREA_HALLWAY = "hallway"


def create_mock_hass():
    """Create a mock HomeAssistant instance without spec restrictions."""
    hass = MagicMock()
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.services.async_call = AsyncMock()
    return hass


class TestVentControllerInit:
    """Tests for VentController initialization."""

    def test_default_values(self):
        """Test default initialization values."""
        hass = create_mock_hass()
        controller = VentController(hass)

        assert controller.min_vents_open == 5
        assert controller.vent_open_delay_seconds == 30
        assert controller.vent_debounce_seconds == 30

    def test_custom_values(self):
        """Test custom initialization values."""
        hass = create_mock_hass()
        controller = VentController(
            hass,
            min_vents_open=3,
            vent_open_delay_seconds=60,
            vent_debounce_seconds=45,
        )

        assert controller.min_vents_open == 3
        assert controller.vent_open_delay_seconds == 60
        assert controller.vent_debounce_seconds == 45

    def test_setters(self):
        """Test property setters."""
        hass = create_mock_hass()
        controller = VentController(hass)

        controller.min_vents_open = 10
        controller.vent_open_delay_seconds = 120
        controller.vent_debounce_seconds = 90

        assert controller.min_vents_open == 10
        assert controller.vent_open_delay_seconds == 120
        assert controller.vent_debounce_seconds == 90


class TestGroupDetection:
    """Tests for vent group detection."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass)

    def test_single_vent_not_group(self, controller):
        """Test that a single vent is not detected as a group."""
        mock_state = MagicMock()
        mock_state.attributes = {}
        controller.hass.states.get.return_value = mock_state

        assert controller.is_cover_group(TEST_VENT_1) is False
        assert controller.get_group_member_count(TEST_VENT_1) == 1

    def test_group_detected(self, controller):
        """Test that a cover group is detected."""
        mock_state = MagicMock()
        mock_state.attributes = {
            ATTR_ENTITY_ID: ["cover.vent_1", "cover.vent_2", "cover.vent_3"]
        }
        controller.hass.states.get.return_value = mock_state

        assert controller.is_cover_group(TEST_VENT_GROUP) is True
        assert controller.get_group_member_count(TEST_VENT_GROUP) == 3

    def test_none_state_returns_defaults(self, controller):
        """Test that None state returns default values."""
        controller.hass.states.get.return_value = None

        assert controller.is_cover_group(TEST_VENT_1) is False
        assert controller.get_group_member_count(TEST_VENT_1) == 1


class TestVentCurrentState:
    """Tests for getting current vent state."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass)

    def test_open_state(self, controller):
        """Test vent in open state."""
        mock_state = MagicMock()
        mock_state.state = STATE_OPEN
        mock_state.attributes = {}
        controller.hass.states.get.return_value = mock_state

        assert controller.get_vent_current_state(TEST_VENT_1) is True

    def test_closed_state(self, controller):
        """Test vent in closed state."""
        mock_state = MagicMock()
        mock_state.state = STATE_CLOSED
        mock_state.attributes = {}
        controller.hass.states.get.return_value = mock_state

        assert controller.get_vent_current_state(TEST_VENT_1) is False

    def test_tilt_position_open(self, controller):
        """Test vent with high tilt position is considered open."""
        mock_state = MagicMock()
        mock_state.state = STATE_CLOSED  # State says closed
        mock_state.attributes = {"current_tilt_position": 75}  # But tilt is open
        controller.hass.states.get.return_value = mock_state

        assert controller.get_vent_current_state(TEST_VENT_1) is True

    def test_tilt_position_closed(self, controller):
        """Test vent with low tilt position is considered closed."""
        mock_state = MagicMock()
        mock_state.state = STATE_CLOSED
        mock_state.attributes = {"current_tilt_position": 25}
        controller.hass.states.get.return_value = mock_state

        assert controller.get_vent_current_state(TEST_VENT_1) is False

    def test_unavailable_state(self, controller):
        """Test unavailable vent returns False."""
        mock_state = MagicMock()
        mock_state.state = STATE_UNAVAILABLE
        controller.hass.states.get.return_value = mock_state

        assert controller.get_vent_current_state(TEST_VENT_1) is False

    def test_none_state(self, controller):
        """Test None state returns False."""
        controller.hass.states.get.return_value = None

        assert controller.get_vent_current_state(TEST_VENT_1) is False


class TestDebounce:
    """Tests for command debouncing."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass, vent_debounce_seconds=30)

    def test_no_previous_command(self, controller):
        """Test that command is allowed with no previous command."""
        can_send, reason = controller.can_send_command(TEST_VENT_1)
        assert can_send is True
        assert "No previous command" in reason

    def test_within_debounce_period(self, controller):
        """Test that command is blocked within debounce period."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        controller._last_command_times[TEST_VENT_1] = now - timedelta(seconds=15)

        can_send, reason = controller.can_send_command(TEST_VENT_1, now)
        assert can_send is False
        assert "Debounce" in reason

    def test_after_debounce_period(self, controller):
        """Test that command is allowed after debounce period."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        controller._last_command_times[TEST_VENT_1] = now - timedelta(seconds=35)

        can_send, reason = controller.can_send_command(TEST_VENT_1, now)
        assert can_send is True
        assert "passed" in reason


class TestEvaluateAreaVents:
    """Tests for area vent evaluation."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(
            hass,
            min_vents_open=5,
            vent_open_delay_seconds=30,
        )

    def _setup_single_vent(self, controller, is_open: bool = False):
        """Set up mock for a single vent."""
        mock_state = MagicMock()
        mock_state.state = STATE_OPEN if is_open else STATE_CLOSED
        mock_state.attributes = {}
        controller.hass.states.get.return_value = mock_state

    def test_critical_room_vents_open(self, controller):
        """Test that critical rooms have vents open."""
        self._setup_single_vent(controller, is_open=False)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=False,
            is_occupied=False,
            is_satiated=False,
            is_critical=True,
            occupancy_start_time=None,
            distance_from_target=5.0,
            now=now,
        )

        assert area_state.should_open is True
        assert "Critical" in area_state.open_reason
        assert area_state.vents[0].should_be_open is True

    def test_active_unsatiated_vents_open(self, controller):
        """Test that active unsatiated rooms have vents open."""
        self._setup_single_vent(controller, is_open=False)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=True,
            is_occupied=True,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=now - timedelta(minutes=10),
            distance_from_target=2.0,
            now=now,
        )

        # When occupied past delay, the reason will mention "Occupied"
        # Either "Active" or "Occupied" reason is valid - vents should be open
        assert area_state.should_open is True
        assert "Active" in area_state.open_reason or "Occupied" in area_state.open_reason

    def test_active_satiated_but_occupied_past_delay_vents_open(self, controller):
        """Test that rooms occupied past delay have vents open even if satiated."""
        self._setup_single_vent(controller, is_open=True)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Room is satiated but occupied for 10 minutes - vents stay open for comfort
        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=True,
            is_occupied=True,
            is_satiated=True,
            is_critical=False,
            occupancy_start_time=now - timedelta(minutes=10),
            distance_from_target=0.0,
            now=now,
        )

        # Vents should be OPEN because room is occupied past delay (comfort)
        assert area_state.should_open is True
        assert "Occupied" in area_state.open_reason

    def test_satiated_not_occupied_vents_closed(self, controller):
        """Test that satiated rooms without occupancy have vents closed."""
        self._setup_single_vent(controller, is_open=True)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=True,
            is_occupied=False,
            is_satiated=True,
            is_critical=False,
            occupancy_start_time=None,
            distance_from_target=0.0,
            now=now,
        )

        assert area_state.should_open is False
        assert "Satiated" in area_state.open_reason

    def test_occupied_past_delay_vents_open(self, controller):
        """Test that rooms occupied past the delay have vents open."""
        self._setup_single_vent(controller, is_open=False)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=False,
            is_occupied=True,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=now - timedelta(seconds=45),  # 45 > 30 delay
            distance_from_target=2.0,
            now=now,
        )

        assert area_state.should_open is True
        assert "Occupied" in area_state.open_reason

    def test_occupied_before_delay_vents_closed(self, controller):
        """Test that rooms occupied before the delay have vents closed."""
        self._setup_single_vent(controller, is_open=False)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=False,
            is_occupied=True,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=now - timedelta(seconds=15),  # 15 < 30 delay
            distance_from_target=2.0,
            now=now,
        )

        assert area_state.should_open is False
        assert "Occupied only" in area_state.open_reason

    def test_inactive_vents_closed(self, controller):
        """Test that inactive rooms have vents closed."""
        self._setup_single_vent(controller, is_open=True)
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=False,
            is_occupied=False,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=None,
            distance_from_target=None,
            now=now,
        )

        assert area_state.should_open is False
        assert "Inactive" in area_state.open_reason

    def test_per_area_vent_delay_override(self, controller):
        """Test that per-area vent delay override is respected."""
        self._setup_single_vent(controller, is_open=False)
        now = datetime(2024, 1, 1, 12, 0, 0)

        # With default delay of 30s, 20s would be too short
        # But with override of 15s, 20s is enough
        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[TEST_VENT_1],
            is_active=False,
            is_occupied=True,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=now - timedelta(seconds=20),
            distance_from_target=2.0,
            area_vent_open_delay=15,  # Override: 15 seconds
            now=now,
        )

        assert area_state.should_open is True

    def test_vent_group_member_count(self, controller):
        """Test that vent groups are counted correctly."""
        mock_state = MagicMock()
        mock_state.state = STATE_CLOSED
        mock_state.attributes = {
            ATTR_ENTITY_ID: ["cover.vent_1", "cover.vent_2"]
        }
        controller.hass.states.get.return_value = mock_state
        now = datetime(2024, 1, 1, 12, 0, 0)

        area_state = controller.evaluate_area_vents(
            area_id=TEST_AREA_HALLWAY,
            area_name="Hallway",
            vents=[TEST_VENT_GROUP],
            is_active=True,
            is_occupied=True,
            is_satiated=False,
            is_critical=False,
            occupancy_start_time=now - timedelta(minutes=10),
            distance_from_target=2.0,
            now=now,
        )

        assert area_state.total_vent_count == 2  # Group has 2 members
        assert area_state.vents[0].is_group is True
        assert area_state.vents[0].member_count == 2


class TestMinimumVentsOpen:
    """Tests for minimum vents open logic."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(
            hass,
            min_vents_open=5,
            vent_open_delay_seconds=30,
            vent_debounce_seconds=30,
        )

    def _setup_vents(self, controller, vent_configs: dict):
        """Set up mock vents.
        
        Args:
            vent_configs: Dict of entity_id -> {"is_open": bool, "members": int}
        """
        def get_state(entity_id):
            if entity_id in vent_configs:
                config = vent_configs[entity_id]
                mock_state = MagicMock()
                mock_state.state = STATE_OPEN if config.get("is_open") else STATE_CLOSED
                members = config.get("members", 1)
                if members > 1:
                    mock_state.attributes = {
                        ATTR_ENTITY_ID: [f"cover.vent_{i}" for i in range(members)]
                    }
                else:
                    mock_state.attributes = {}
                return mock_state
            return None
        
        controller.hass.states.get.side_effect = get_state

    def test_minimum_vents_kept_open(self, controller):
        """Test that minimum vents are kept open for back pressure prevention."""
        # Set up 6 single vents, all would normally close
        vent_configs = {
            "cover.vent_1": {"is_open": True, "members": 1},
            "cover.vent_2": {"is_open": True, "members": 1},
            "cover.vent_3": {"is_open": True, "members": 1},
            "cover.vent_4": {"is_open": True, "members": 1},
            "cover.vent_5": {"is_open": True, "members": 1},
            "cover.vent_6": {"is_open": True, "members": 1},
        }
        self._setup_vents(controller, vent_configs)

        area_vent_configs = {
            "area_1": ["cover.vent_1", "cover.vent_2"],
            "area_2": ["cover.vent_3", "cover.vent_4"],
            "area_3": ["cover.vent_5", "cover.vent_6"],
        }

        # All areas inactive - normally all vents would close
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
            room_temp_states={},
        )

        # But minimum 5 should be kept open
        vents_to_stay_open = sum(
            1 for area_state in control_state.area_states.values()
            for vent in area_state.vents
            if vent.should_be_open
        )
        assert vents_to_stay_open >= 5

    def test_group_counts_as_multiple_vents(self, controller):
        """Test that a group of 3 vents counts as 3 toward minimum."""
        # Set up a group of 3 vents
        vent_configs = {
            "cover.vent_group": {"is_open": True, "members": 3},
            "cover.vent_single": {"is_open": True, "members": 1},
        }
        self._setup_vents(controller, vent_configs)

        area_vent_configs = {
            "area_1": ["cover.vent_group"],
            "area_2": ["cover.vent_single"],
        }

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
        )

        # Total vents should be 4 (3 from group + 1 single)
        assert control_state.total_vents == 4


class TestEvaluateAllVents:
    """Tests for full vent evaluation."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(
            hass,
            min_vents_open=2,
            vent_open_delay_seconds=30,
            vent_debounce_seconds=30,
        )

    def _setup_vents(self, controller, vent_states: dict):
        """Set up mock vent states."""
        def get_state(entity_id):
            if entity_id in vent_states:
                mock_state = MagicMock()
                mock_state.state = STATE_OPEN if vent_states[entity_id] else STATE_CLOSED
                mock_state.attributes = {}
                return mock_state
            return None
        
        controller.hass.states.get.side_effect = get_state

    def test_pending_commands_generated(self, controller):
        """Test that pending commands are generated for state changes."""
        self._setup_vents(controller, {TEST_VENT_1: False})  # Currently closed

        area_vent_configs = {TEST_AREA_BEDROOM: [TEST_VENT_1]}
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Create an active area that needs the vent open
        active_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            occupancy_start_time=now - timedelta(minutes=10),
        )

        # Create a room temp state that's unsatiated
        room_temp_state = RoomTemperatureState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_satiated=False,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[active_area],
            occupied_areas=[active_area],
            room_temp_states={TEST_AREA_BEDROOM: room_temp_state},
            now=now,
        )

        # Should have a command to open the vent
        assert len(control_state.pending_commands) == 1
        entity_id, should_open, reason = control_state.pending_commands[0]
        assert entity_id == TEST_VENT_1
        assert should_open is True

    def test_no_commands_when_state_matches(self, controller):
        """Test that no commands are generated when state already matches."""
        self._setup_vents(controller, {TEST_VENT_1: True})  # Already open

        area_vent_configs = {TEST_AREA_BEDROOM: [TEST_VENT_1]}
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Create an active area that needs the vent open
        active_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            occupancy_start_time=now - timedelta(minutes=10),
        )

        room_temp_state = RoomTemperatureState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_satiated=False,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[active_area],
            occupied_areas=[active_area],
            room_temp_states={TEST_AREA_BEDROOM: room_temp_state},
            now=now,
        )

        # No commands needed - vent is already open
        assert len(control_state.pending_commands) == 0


class TestExecuteVentCommands:
    """Tests for executing vent commands."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass)

    @pytest.mark.asyncio
    async def test_open_command_executed(self, controller):
        """Test that open tilt command is executed."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        control_state = VentControlState(
            pending_commands=[(TEST_VENT_1, True, "Test open")]
        )

        executed = await controller.async_execute_vent_commands(control_state, now)

        assert executed == 1
        controller.hass.services.async_call.assert_called_once_with(
            "cover",
            SERVICE_OPEN_COVER_TILT,
            {ATTR_ENTITY_ID: TEST_VENT_1},
            blocking=True,
        )
        assert TEST_VENT_1 in controller._last_command_times

    @pytest.mark.asyncio
    async def test_close_command_executed(self, controller):
        """Test that close tilt command is executed."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        control_state = VentControlState(
            pending_commands=[(TEST_VENT_1, False, "Test close")]
        )

        executed = await controller.async_execute_vent_commands(control_state, now)

        assert executed == 1
        controller.hass.services.async_call.assert_called_once_with(
            "cover",
            SERVICE_CLOSE_COVER_TILT,
            {ATTR_ENTITY_ID: TEST_VENT_1},
            blocking=True,
        )

    @pytest.mark.asyncio
    async def test_multiple_commands_executed(self, controller):
        """Test that multiple commands are executed."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        control_state = VentControlState(
            pending_commands=[
                (TEST_VENT_1, True, "Open 1"),
                (TEST_VENT_2, False, "Close 2"),
                (TEST_VENT_3, True, "Open 3"),
            ]
        )

        executed = await controller.async_execute_vent_commands(control_state, now)

        assert executed == 3
        assert controller.hass.services.async_call.call_count == 3

    @pytest.mark.asyncio
    async def test_command_error_handled(self, controller):
        """Test that command errors are handled gracefully."""
        controller.hass.services.async_call.side_effect = Exception("Test error")

        now = datetime(2024, 1, 1, 12, 0, 0)
        control_state = VentControlState(
            pending_commands=[(TEST_VENT_1, True, "Test open")]
        )

        executed = await controller.async_execute_vent_commands(control_state, now)

        assert executed == 0  # Command failed


class TestGetSummary:
    """Tests for summary generation."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass, min_vents_open=5)

    def test_summary_structure(self, controller):
        """Test that summary has expected structure."""
        vent_state = VentState(
            entity_id=TEST_VENT_1,
            area_id=TEST_AREA_BEDROOM,
            is_group=False,
            member_count=1,
            is_open=True,
            should_be_open=True,
            open_reason="Active",
        )

        area_state = AreaVentState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            vents=[vent_state],
            total_vent_count=1,
            open_vent_count=1,
            should_open=True,
            open_reason="Active",
        )

        control_state = VentControlState(
            total_vents=1,
            open_vents=1,
            vents_should_be_open=1,
            area_states={TEST_AREA_BEDROOM: area_state},
        )

        summary = controller.get_summary(control_state)

        assert "total_vents" in summary
        assert "open_vents" in summary
        assert "vents_should_be_open" in summary
        assert "min_vents_required" in summary
        assert "pending_commands" in summary
        assert "areas" in summary
        assert TEST_AREA_BEDROOM in summary["areas"]

        area_summary = summary["areas"][TEST_AREA_BEDROOM]
        assert area_summary["area_name"] == "Bedroom"
        assert area_summary["should_open"] is True
        assert len(area_summary["vents"]) == 1
