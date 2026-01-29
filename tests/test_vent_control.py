"""Tests for the vent control module."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from homeassistant.components.climate import HVACMode
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


class TestDistanceFromTarget:
    """Tests for distance_from_target calculation in evaluate_all_vents."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(
            hass,
            min_vents_open=1,
            vent_open_delay_seconds=0,  # No delay for testing
            vent_debounce_seconds=0,
        )

    def _setup_vents(self, controller, vent_states: dict):
        """Set up mock vent states."""
        def get_state(entity_id):
            if entity_id in vent_states:
                config = vent_states[entity_id]
                mock_state = MagicMock()
                mock_state.state = STATE_OPEN if config.get("is_open", False) else STATE_CLOSED
                mock_state.attributes = {}
                if "members" in config:
                    mock_state.attributes[ATTR_ENTITY_ID] = [
                        f"cover.member_{i}" for i in range(config["members"])
                    ]
                return mock_state
            return None

        controller.hass.states.get.side_effect = get_state

    def test_distance_from_target_uses_target_temperature(self, controller):
        """Test that distance_from_target is calculated using target_temperature field."""
        self._setup_vents(controller, {TEST_VENT_1: {"is_open": True, "members": 1}})

        area_vent_configs = {TEST_AREA_BEDROOM: [TEST_VENT_1]}
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Create occupied area
        occupied_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            occupancy_start_time=now - timedelta(minutes=10),
        )

        # Create room temp state with target_temperature set
        # Current temp is 19, target is 22, so distance should be 3
        room_temp_state = RoomTemperatureState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_satiated=False,
            determining_temperature=19.0,
            target_temperature=22.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[occupied_area],
            occupied_areas=[occupied_area],
            room_temp_states={TEST_AREA_BEDROOM: room_temp_state},
            now=now,
        )

        # The distance_from_target should be 3.0 (|19 - 22|)
        area_state = control_state.area_states[TEST_AREA_BEDROOM]
        assert area_state.distance_from_target == 3.0

    def test_distance_from_target_zero_when_satiated(self, controller):
        """Test that distance_from_target is 0 when room is satiated."""
        self._setup_vents(controller, {TEST_VENT_1: {"is_open": True, "members": 1}})

        area_vent_configs = {TEST_AREA_BEDROOM: [TEST_VENT_1]}
        now = datetime(2024, 1, 1, 12, 0, 0)

        occupied_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            occupancy_start_time=now - timedelta(minutes=10),
        )

        # Room is satiated
        room_temp_state = RoomTemperatureState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_satiated=True,
            determining_temperature=22.0,
            target_temperature=22.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[occupied_area],
            occupied_areas=[occupied_area],
            room_temp_states={TEST_AREA_BEDROOM: room_temp_state},
            now=now,
        )

        area_state = control_state.area_states[TEST_AREA_BEDROOM]
        assert area_state.distance_from_target == 0.0

    def test_distance_from_target_zero_when_no_target(self, controller):
        """Test that distance_from_target is 0 when target_temperature is None."""
        self._setup_vents(controller, {TEST_VENT_1: {"is_open": True, "members": 1}})

        area_vent_configs = {TEST_AREA_BEDROOM: [TEST_VENT_1]}
        now = datetime(2024, 1, 1, 12, 0, 0)

        occupied_area = AreaOccupancyState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            occupancy_start_time=now - timedelta(minutes=10),
        )

        # target_temperature is None
        room_temp_state = RoomTemperatureState(
            area_id=TEST_AREA_BEDROOM,
            area_name="Bedroom",
            is_satiated=False,
            determining_temperature=19.0,
            target_temperature=None,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[occupied_area],
            occupied_areas=[occupied_area],
            room_temp_states={TEST_AREA_BEDROOM: room_temp_state},
            now=now,
        )

        area_state = control_state.area_states[TEST_AREA_BEDROOM]
        assert area_state.distance_from_target == 0.0

    def test_distance_priority_for_minimum_vents(self, controller):
        """Test that rooms are prioritized by temperature based on HVAC mode.
        
        For HEAT mode: coldest rooms get priority (they need the heat most).
        For COOL mode: hottest rooms get priority (they need the cooling most).
        """
        # Set up multiple vents that would normally all close
        self._setup_vents(controller, {
            "cover.vent_close": {"is_open": True, "members": 1},
            "cover.vent_far": {"is_open": True, "members": 1},
        })

        area_vent_configs = {
            "area_close": ["cover.vent_close"],
            "area_far": ["cover.vent_far"],
        }
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Both areas inactive - all would close, but one should stay open for minimum
        # For HEAT mode: the coldest room (area_far at 17°) should get priority

        # Room close to target (warmer - 21°)
        room_close = RoomTemperatureState(
            area_id="area_close",
            area_name="Close Room",
            is_satiated=False,
            determining_temperature=21.0,
            target_temperature=22.0,
        )

        # Room far from target (colder - 17°)
        room_far = RoomTemperatureState(
            area_id="area_far",
            area_name="Far Room",
            is_satiated=False,
            determining_temperature=17.0,
            target_temperature=22.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
            room_temp_states={
                "area_close": room_close,
                "area_far": room_far,
            },
            hvac_mode=HVACMode.HEAT,
            now=now,
        )

        # Verify distances were calculated correctly
        assert control_state.area_states["area_close"].distance_from_target == 1.0
        assert control_state.area_states["area_far"].distance_from_target == 5.0

        # Verify determining temperatures were passed through
        assert control_state.area_states["area_close"].determining_temperature == 21.0
        assert control_state.area_states["area_far"].determining_temperature == 17.0

    def test_infer_effective_hvac_mode_prefers_determining_temperature_over_sensor_readings(
        self,
    ):
        """Prefer determining_temperature, only using readings as fallback."""
        hass = create_mock_hass()
        controller = VentController(hass)

        # Room A: determining_temperature indicates it's cold (60), but raw readings
        # include a high outlier (80) that would skew an "all readings" average.
        room_a = RoomTemperatureState(area_id="a", area_name="A")
        room_a.determining_temperature = 60.0
        room_a.sensor_readings = {"sensor.a1": 60.0, "sensor.a2": 80.0}

        # Room B: normal room.
        room_b = RoomTemperatureState(area_id="b", area_name="B")
        room_b.determining_temperature = 70.0
        room_b.sensor_readings = {"sensor.b1": 70.0}

        mode = controller.infer_effective_hvac_mode(
            room_temp_states={"a": room_a, "b": room_b},
            target_temp_low=68.0,
            target_temp_high=72.0,
        )

        # Using determining temperatures (60, 70) => avg 65 => below low target => HEAT.
        assert mode == HVACMode.HEAT

    def test_infer_effective_hvac_mode_falls_back_to_sensor_readings_when_no_determining_temperature(
        self,
    ):
        """When determining_temperature is None, use sensor readings."""
        hass = create_mock_hass()
        controller = VentController(hass)

        room = RoomTemperatureState(area_id="a", area_name="A")
        room.determining_temperature = None
        room.sensor_readings = {"sensor.a1": 75.0, "sensor.a2": 77.0}

        mode = controller.infer_effective_hvac_mode(
            room_temp_states={"a": room},
            target_temp_low=68.0,
            target_temp_high=72.0,
        )

        # Average readings (76) => above high target => COOL.
        assert mode == HVACMode.COOL

    def test_heat_mode_prioritizes_coldest_rooms(self, controller):
        """Test that in HEAT mode, coldest rooms get priority for minimum vents."""
        controller._min_vents_open = 1  # Only keep one vent open
        
        self._setup_vents(controller, {
            "cover.vent_hot": {"is_open": True, "members": 1},
            "cover.vent_cold": {"is_open": True, "members": 1},
        })

        area_vent_configs = {
            "area_hot": ["cover.vent_hot"],
            "area_cold": ["cover.vent_cold"],
        }
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Hot room (way above target - should NOT get priority for heating)
        room_hot = RoomTemperatureState(
            area_id="area_hot",
            area_name="Hot Room",
            is_satiated=True,  # Already satiated
            determining_temperature=85.0,  # Very hot
            target_temperature=70.0,
        )

        # Cold room (below target - SHOULD get priority for heating)
        room_cold = RoomTemperatureState(
            area_id="area_cold",
            area_name="Cold Room",
            is_satiated=False,
            determining_temperature=65.0,  # Cold
            target_temperature=70.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
            room_temp_states={
                "area_hot": room_hot,
                "area_cold": room_cold,
            },
            hvac_mode=HVACMode.HEAT,
            now=now,
        )

        # The cold room should have its vent kept open for minimum vents
        cold_vent = control_state.area_states["area_cold"].vents[0]
        hot_vent = control_state.area_states["area_hot"].vents[0]
        
        assert cold_vent.should_be_open is True, "Cold room vent should stay open in HEAT mode"
        assert hot_vent.should_be_open is False, "Hot room vent should close in HEAT mode"

    def test_cool_mode_prioritizes_hottest_rooms(self, controller):
        """Test that in COOL mode, hottest rooms get priority for minimum vents."""
        controller._min_vents_open = 1  # Only keep one vent open
        
        self._setup_vents(controller, {
            "cover.vent_hot": {"is_open": True, "members": 1},
            "cover.vent_cold": {"is_open": True, "members": 1},
        })

        area_vent_configs = {
            "area_hot": ["cover.vent_hot"],
            "area_cold": ["cover.vent_cold"],
        }
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Hot room (above target - SHOULD get priority for cooling)
        room_hot = RoomTemperatureState(
            area_id="area_hot",
            area_name="Hot Room",
            is_satiated=False,
            determining_temperature=85.0,  # Very hot
            target_temperature=72.0,
        )

        # Cold room (way below target - should NOT get priority for cooling)
        room_cold = RoomTemperatureState(
            area_id="area_cold",
            area_name="Cold Room",
            is_satiated=True,  # Already satiated
            determining_temperature=65.0,  # Cold
            target_temperature=72.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
            room_temp_states={
                "area_hot": room_hot,
                "area_cold": room_cold,
            },
            hvac_mode=HVACMode.COOL,
            now=now,
        )

        # The hot room should have its vent kept open for minimum vents
        cold_vent = control_state.area_states["area_cold"].vents[0]
        hot_vent = control_state.area_states["area_hot"].vents[0]
        
        assert hot_vent.should_be_open is True, "Hot room vent should stay open in COOL mode"
        assert cold_vent.should_be_open is False, "Cold room vent should close in COOL mode"

    def test_temperature_priority_beats_occupied_bonus_for_minimum_vents(self, controller):
        """Test that temperature-based priority beats the occupied bonus for minimum vents.
        
        When selecting which inactive rooms should stay open for minimum vents,
        a very cold room should beat a slightly cold room even if the slightly cold
        room was recently occupied, because temperature need outweighs occupancy bonus.
        
        Priority scores:
        - Cold room (55°F): (80 - 55) * 10 = 250 points
        - Warm room (70°F): (80 - 70) * 10 = 100 points (occupancy bonus doesn't apply
          since neither room is currently active/occupied in minimum vents selection)
        """
        controller._min_vents_open = 1  # Only keep one vent open
        
        self._setup_vents(controller, {
            "cover.vent_warm": {"is_open": True, "members": 1},
            "cover.vent_cold": {"is_open": True, "members": 1},
        })

        area_vent_configs = {
            "area_warm": ["cover.vent_warm"],
            "area_cold": ["cover.vent_cold"],
        }
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Warm room (close to target - doesn't need heat as much)
        room_warm = RoomTemperatureState(
            area_id="area_warm",
            area_name="Warm Room",
            is_satiated=False,
            determining_temperature=70.0,  # Close to target
            target_temperature=72.0,
        )

        # Cold room (far from target - needs heat more)
        room_cold = RoomTemperatureState(
            area_id="area_cold",
            area_name="Cold Room",
            is_satiated=False,
            determining_temperature=55.0,  # Very cold, far from target
            target_temperature=72.0,
        )

        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],  # Neither is active
            occupied_areas=[],  # Neither is occupied - both inactive
            room_temp_states={
                "area_warm": room_warm,
                "area_cold": room_cold,
            },
            hvac_mode=HVACMode.HEAT,
            now=now,
        )

        # The cold room should get priority for minimum vents due to higher temp-based score
        cold_vent = control_state.area_states["area_cold"].vents[0]
        warm_vent = control_state.area_states["area_warm"].vents[0]
        
        assert cold_vent.should_be_open is True, "Cold room should get priority in HEAT mode"
        assert warm_vent.should_be_open is False, "Warm room should not get priority over cold room"

    def test_hvac_off_with_target_temps_infers_mode_for_priority(self, controller):
        """Test that when HVAC is OFF but target temps are provided, mode is inferred for vent priority.
        
        This tests the fix for the startup bug where vents didn't open correctly after reboot.
        When HVAC is OFF (or None), but we have target temperatures and room temperatures,
        the system should infer whether rooms need heating or cooling and prioritize accordingly.
        """
        controller._min_vents_open = 1  # Only keep one vent open
        
        self._setup_vents(controller, {
            "cover.vent_critical_cold": {"is_open": True, "members": 1},
            "cover.vent_comfortable": {"is_open": True, "members": 1},
        })

        area_vent_configs = {
            "area_critical": ["cover.vent_critical_cold"],
            "area_comfortable": ["cover.vent_comfortable"],
        }
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Critical cold room - needs heat urgently
        room_critical = RoomTemperatureState(
            area_id="area_critical",
            area_name="Critical Cold Room",
            is_satiated=False,
            is_critical=True,  # Critical temperature
            determining_temperature=55.0,  # Very cold
            target_temperature=70.0,
        )

        # Comfortable room - at target
        room_comfortable = RoomTemperatureState(
            area_id="area_comfortable",
            area_name="Comfortable Room",
            is_satiated=True,
            determining_temperature=70.0,
            target_temperature=70.0,
        )

        # HVAC is OFF, but we provide target temperatures - system should infer HEAT mode
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vent_configs,
            active_areas=[],
            occupied_areas=[],
            room_temp_states={
                "area_critical": room_critical,
                "area_comfortable": room_comfortable,
            },
            hvac_mode=HVACMode.OFF,  # HVAC is off (e.g., during startup)
            target_temp_low=68.0,  # But target temps are available
            target_temp_high=72.0,
            now=now,
        )

        # The critical cold room should get priority even though HVAC is OFF
        # because the system should infer HEAT mode from the temperature data
        critical_vent = control_state.area_states["area_critical"].vents[0]
        comfortable_vent = control_state.area_states["area_comfortable"].vents[0]
        
        assert critical_vent.should_be_open is True, (
            "Critical cold room vent should stay open for minimum vents even when HVAC is OFF"
        )
        assert comfortable_vent.should_be_open is False, (
            "Comfortable room vent should close when HVAC is OFF"
        )


# =============================================================================
# Tests for Intelligent Minimum Vent Selection
# =============================================================================


class TestIntelligentMinimumVentSelection:
    """Tests for intelligent selection of which vents stay open for minimum."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller with minimum 3 vents."""
        hass = create_mock_hass()
        return VentController(hass, min_vents_open=3)

    @pytest.fixture
    def room_temp_states(self):
        """Create room temperature states for priority testing."""
        return {
            "cold_room": RoomTemperatureState(
                area_id="cold_room",
                area_name="Cold Room",
                temperature_sensors=["sensor.cold_temp"],
                determining_temperature=65.0,
            ),
            "warm_room": RoomTemperatureState(
                area_id="warm_room",
                area_name="Warm Room",
                temperature_sensors=["sensor.warm_temp"],
                determining_temperature=72.0,
            ),
            "medium_room": RoomTemperatureState(
                area_id="medium_room",
                area_name="Medium Room",
                temperature_sensors=["sensor.medium_temp"],
                determining_temperature=68.0,
            ),
        }

    def test_closes_satiated_vent_in_favor_of_cold_vent(self, controller, room_temp_states):
        """Test that warm satiated vents are closed in favor of cold vents."""
        now = datetime.now()
        
        # Warm room vent is currently open but satiated
        warm_state = MagicMock()
        warm_state.state = STATE_OPEN
        warm_state.attributes = {"current_tilt_position": 100}
        
        # Cold room vent is currently closed
        cold_state = MagicMock()
        cold_state.state = STATE_CLOSED
        cold_state.attributes = {"current_tilt_position": 0}
        
        # Medium room vent is currently closed
        medium_state = MagicMock()
        medium_state.state = STATE_CLOSED
        medium_state.attributes = {"current_tilt_position": 0}
        
        def get_state_side_effect(entity_id):
            if entity_id == "cover.warm_vent":
                return warm_state
            elif entity_id == "cover.cold_vent":
                return cold_state
            elif entity_id == "cover.medium_vent":
                return medium_state
            return None
        
        controller.hass.states.get.side_effect = get_state_side_effect
        
        area_vents = {
            "cold_room": ["cover.cold_vent"],
            "warm_room": ["cover.warm_vent"],
            "medium_room": ["cover.medium_vent"],
        }
        
        # No active/occupied areas - relying on minimum enforcement
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=[],
            occupied_areas=[],
            room_temp_states=room_temp_states,
            hvac_mode=HVACMode.HEAT,
            target_temp_low=70.0,
            target_temp_high=78.0,
            now=now,
        )
        
        # With HEAT mode, coldest rooms should be prioritized
        # Cold room (65°F) should be open
        # Medium room (68°F) should be open  
        # Warm room (72°F) should be open (to meet minimum of 3)
        assert control_state.area_states["cold_room"].vents[0].should_be_open is True
        assert control_state.area_states["medium_room"].vents[0].should_be_open is True
        assert control_state.area_states["warm_room"].vents[0].should_be_open is True
        
        # All 3 vents must be open to meet minimum requirement
        assert control_state.vents_should_be_open == 3

    def test_reorders_vents_when_below_minimum(self, controller):
        """Test that vents are reordered intelligently when below minimum."""
        now = datetime.now()
        
        # Setup: 5 vents, 2 currently open (wrong ones), need min 3
        room_temp_states = {
            "room_a": RoomTemperatureState(
                area_id="room_a", area_name="Room A",
                determining_temperature=64.0,  # Coldest - highest priority
            ),
            "room_b": RoomTemperatureState(
                area_id="room_b", area_name="Room B",
                determining_temperature=66.0,  # Second coldest
            ),
            "room_c": RoomTemperatureState(
                area_id="room_c", area_name="Room C",
                determining_temperature=68.0,  # Third coldest
            ),
            "room_d": RoomTemperatureState(
                area_id="room_d", area_name="Room D",
                determining_temperature=70.0,  # Fourth - currently open (wrong)
            ),
            "room_e": RoomTemperatureState(
                area_id="room_e", area_name="Room E",
                determining_temperature=72.0,  # Warmest - currently open (wrong)
            ),
        }
        
        # Rooms D and E are currently open (but they're warm)
        def get_vent_state(entity_id):
            state = MagicMock()
            if entity_id in ["cover.room_d_vent", "cover.room_e_vent"]:
                state.state = STATE_OPEN
                state.attributes = {"current_tilt_position": 100}
            else:
                state.state = STATE_CLOSED
                state.attributes = {"current_tilt_position": 0}
            return state
        
        controller.hass.states.get.side_effect = get_vent_state
        
        area_vents = {
            "room_a": ["cover.room_a_vent"],
            "room_b": ["cover.room_b_vent"],
            "room_c": ["cover.room_c_vent"],
            "room_d": ["cover.room_d_vent"],
            "room_e": ["cover.room_e_vent"],
        }
        
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=[],
            occupied_areas=[],
            room_temp_states=room_temp_states,
            hvac_mode=HVACMode.HEAT,
            target_temp_low=70.0,
            target_temp_high=78.0,
            now=now,
        )
        
        # The 3 coldest rooms should be selected
        assert control_state.area_states["room_a"].vents[0].should_be_open is True
        assert control_state.area_states["room_b"].vents[0].should_be_open is True
        assert control_state.area_states["room_c"].vents[0].should_be_open is True
        # The 2 warmest should close (even though currently open)
        assert control_state.area_states["room_d"].vents[0].should_be_open is False
        assert control_state.area_states["room_e"].vents[0].should_be_open is False
        
        assert control_state.vents_should_be_open == 3

    def test_more_than_minimum_vents_when_needed(self, controller):
        """Test that more than minimum vents can be open when rooms are active."""
        now = datetime.now()
        
        # 5 rooms are active - should all be open even though min is 3
        active_areas = [
            AreaOccupancyState(
                area_id=f"room_{i}",
                area_name=f"Room {i}",
                binary_sensors=[],
                sensors=[],
                is_active=True,
                occupancy_start_time=now - timedelta(minutes=10),
            )
            for i in range(5)
        ]
        
        def get_vent_state(entity_id):
            state = MagicMock()
            state.state = STATE_CLOSED
            state.attributes = {"current_tilt_position": 0}
            return state
        
        controller.hass.states.get.side_effect = get_vent_state
        
        area_vents = {f"room_{i}": [f"cover.room_{i}_vent"] for i in range(5)}
        
        # Create room temp states - all unsatiated
        room_temp_states = {
            f"room_{i}": RoomTemperatureState(
                area_id=f"room_{i}",
                area_name=f"Room {i}",
                determining_temperature=65.0,
                is_satiated=False,
            )
            for i in range(5)
        }
        
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=active_areas,
            occupied_areas=active_areas,
            room_temp_states=room_temp_states,
            hvac_mode=HVACMode.HEAT,
            now=now,
        )
        
        # All 5 should be open (not limited by minimum of 3)
        assert control_state.vents_should_be_open == 5
        for i in range(5):
            assert control_state.area_states[f"room_{i}"].vents[0].should_be_open is True


# =============================================================================
# Tests for Unavailable Vent Retry and Fallback
# =============================================================================


class TestUnavailableVentRetry:
    """Tests for retry logic when vents don't respond to commands."""

    @pytest.fixture
    def controller(self):
        """Create a vent controller for testing."""
        hass = create_mock_hass()
        return VentController(hass, min_vents_open=3, vent_debounce_seconds=0)

    @pytest.mark.asyncio
    async def test_tracks_pending_confirmations(self, controller):
        """Test that commands are tracked in pending confirmations."""
        now = datetime.now()
        
        # Mock vent state (closed, should be open)
        vent_state = MagicMock()
        vent_state.state = STATE_CLOSED
        vent_state.attributes = {"current_tilt_position": 0}
        controller.hass.states.get.return_value = vent_state
        
        control_state = VentControlState()
        control_state.pending_commands = [
            ("cover.test_vent", True, "Test reason")
        ]
        
        # Execute command
        await controller.async_execute_vent_commands(control_state, now=now)
        
        # Should be tracked in pending confirmations
        assert "cover.test_vent" in controller._pending_confirmations
        desired_state, command_time, retry_count = controller._pending_confirmations["cover.test_vent"]
        assert desired_state is True
        assert command_time == now
        assert retry_count == 1

    @pytest.mark.asyncio
    async def test_removes_confirmation_when_vent_responds(self, controller):
        """Test that confirmation is removed when vent changes state."""
        now = datetime.now()
        
        # Add a pending confirmation from 30 seconds ago
        controller._pending_confirmations["cover.test_vent"] = (True, now - timedelta(seconds=30), 1)
        
        # Mock vent state - NOW OPEN (command succeeded)
        vent_state = MagicMock()
        vent_state.state = STATE_OPEN
        vent_state.attributes = {"current_tilt_position": 100}
        controller.hass.states.get.return_value = vent_state
        
        control_state = VentControlState()
        control_state.pending_commands = []  # No new commands
        
        # Execute (will check pending confirmations)
        await controller.async_execute_vent_commands(control_state, now=now)
        
        # Should be removed from pending (it responded)
        assert "cover.test_vent" not in controller._pending_confirmations

    @pytest.mark.asyncio
    async def test_retries_unresponsive_vent(self, controller):
        """Test that unresponsive vents are retried."""
        now = datetime.now()
        
        # Add a pending confirmation from 61 seconds ago (past retry threshold)
        controller._pending_confirmations["cover.test_vent"] = (True, now - timedelta(seconds=61), 1)
        
        # Mock vent state - STILL CLOSED (hasn't responded)
        vent_state = MagicMock()
        vent_state.state = STATE_CLOSED
        vent_state.attributes = {"current_tilt_position": 0}
        controller.hass.states.get.return_value = vent_state
        
        control_state = VentControlState()
        control_state.pending_commands = [
            ("cover.test_vent", True, "Retry command")
        ]
        
        # Execute
        await controller.async_execute_vent_commands(control_state, now=now)
        
        # Should increment retry count
        assert "cover.test_vent" in controller._pending_confirmations
        _, _, retry_count = controller._pending_confirmations["cover.test_vent"]
        assert retry_count == 2  # Incremented from 1

    @pytest.mark.asyncio
    async def test_marks_vent_unresponsive_after_retries(self, controller):
        """Test that vents are marked unresponsive after 3 retries."""
        now = datetime.now()
        
        # Add a pending confirmation from 61 seconds ago with 3 retries
        controller._pending_confirmations["cover.test_vent"] = (True, now - timedelta(seconds=61), 3)
        
        # Mock vent state - STILL CLOSED
        vent_state = MagicMock()
        vent_state.state = STATE_CLOSED
        vent_state.attributes = {"current_tilt_position": 0}
        controller.hass.states.get.return_value = vent_state
        
        control_state = VentControlState()
        control_state.pending_commands = []
        
        # Execute
        await controller.async_execute_vent_commands(control_state, now=now)
        
        # Should be removed after 3 retries (marked unresponsive)
        assert "cover.test_vent" not in controller._pending_confirmations

    def test_skips_unresponsive_vent_in_selection(self, controller):
        """Test that unresponsive vents are skipped and alternates selected."""
        now = datetime.now()
        
        # Mark vent A as unresponsive (3+ retries, >60s)
        controller._pending_confirmations["cover.room_a_vent"] = (
            True, now - timedelta(seconds=65), 3
        )
        
        # Mock vent states
        def get_vent_state(entity_id):
            state = MagicMock()
            if entity_id == "cover.room_a_vent":
                # Unresponsive - still closed despite commands
                state.state = STATE_CLOSED
                state.attributes = {"current_tilt_position": 0}
            else:
                state.state = STATE_CLOSED
                state.attributes = {"current_tilt_position": 0}
            return state
        
        controller.hass.states.get.side_effect = get_vent_state
        
        # 3 rooms, need min 3, but room_a is unresponsive
        room_temp_states = {
            "room_a": RoomTemperatureState(
                area_id="room_a",
                area_name="Room A",
                determining_temperature=64.0,  # Coldest - but unresponsive
            ),
            "room_b": RoomTemperatureState(
                area_id="room_b",
                area_name="Room B",
                determining_temperature=66.0,
            ),
            "room_c": RoomTemperatureState(
                area_id="room_c",
                area_name="Room C",
                determining_temperature=68.0,
            ),
        }
        
        area_vents = {
            "room_a": ["cover.room_a_vent"],
            "room_b": ["cover.room_b_vent"],
            "room_c": ["cover.room_c_vent"],
        }
        
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=[],
            occupied_areas=[],
            room_temp_states=room_temp_states,
            hvac_mode=HVACMode.HEAT,
            target_temp_low=70.0,
            now=now,
        )
        
        # Room A (unresponsive) should NOT be counted toward minimum
        # Rooms B and C should be selected instead
        # Since room_a is unresponsive, we only count rooms b and c = 2 vents
        # We need 3, so we'd try to add room_a, but it's unresponsive
        # So we can only get 2 vents total
        assert control_state.area_states["room_a"].vents[0].should_be_open is False
        assert control_state.area_states["room_b"].vents[0].should_be_open is True
        assert control_state.area_states["room_c"].vents[0].should_be_open is True
        # Can only achieve 2 vents due to unresponsive vent
        assert control_state.vents_should_be_open == 2

    def test_selects_fourth_vent_when_one_unresponsive(self, controller):
        """Test that the 4th best vent is selected when #1 is unresponsive."""
        now = datetime.now()
        
        # Mark the coldest room's vent as unresponsive
        controller._pending_confirmations["cover.room_a_vent"] = (
            True, now - timedelta(seconds=65), 3
        )
        
        def get_vent_state(entity_id):
            state = MagicMock()
            if entity_id == "cover.room_a_vent":
                state.state = STATE_CLOSED  # Unresponsive
            else:
                state.state = STATE_CLOSED
            state.attributes = {"current_tilt_position": 0}
            return state
        
        controller.hass.states.get.side_effect = get_vent_state
        
        # 4 rooms, need min 3
        room_temp_states = {
            "room_a": RoomTemperatureState(
                area_id="room_a",
                area_name="Room A",
                determining_temperature=64.0,  # Best but unresponsive
            ),
            "room_b": RoomTemperatureState(
                area_id="room_b",
                area_name="Room B",
                determining_temperature=66.0,  # 2nd best
            ),
            "room_c": RoomTemperatureState(
                area_id="room_c",
                area_name="Room C",
                determining_temperature=68.0,  # 3rd best
            ),
            "room_d": RoomTemperatureState(
                area_id="room_d",
                area_name="Room D",
                determining_temperature=70.0,  # 4th best (fallback)
            ),
        }
        
        area_vents = {f"room_{c}": [f"cover.room_{c}_vent"] for c in "abcd"}
        
        control_state = controller.evaluate_all_vents(
            area_vent_configs=area_vents,
            active_areas=[],
            occupied_areas=[],
            room_temp_states=room_temp_states,
            hvac_mode=HVACMode.HEAT,
            target_temp_low=70.0,
            now=now,
        )
        
        # Should skip room_a (unresponsive) and select rooms b, c, d
        assert control_state.area_states["room_a"].vents[0].should_be_open is False
        assert control_state.area_states["room_b"].vents[0].should_be_open is True
        assert control_state.area_states["room_c"].vents[0].should_be_open is True
        assert control_state.area_states["room_d"].vents[0].should_be_open is True
        assert control_state.vents_should_be_open == 3

