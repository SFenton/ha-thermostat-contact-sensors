"""Tests for the room occupancy tracking module."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_BINARY_SENSORS,
    CONF_MIN_OCCUPANCY_MINUTES,
    CONF_SENSORS,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_MIN_OCCUPANCY_MINUTES,
)
from custom_components.thermostat_contact_sensors.occupancy import (
    AreaOccupancyState,
    RoomOccupancyTracker,
    get_sensor_occupancy_state,
    is_binary_sensor_occupied,
    is_sensor_occupied,
)


# Test entity IDs for occupancy
TEST_BINARY_SENSOR_MOTION_1 = "binary_sensor.living_room_motion"
TEST_BINARY_SENSOR_MOTION_2 = "binary_sensor.living_room_motion_2"
TEST_BINARY_SENSOR_OCCUPANCY = "binary_sensor.bedroom_occupancy"
TEST_SENSOR_PRESENCE_1 = "sensor.living_room_presence"
TEST_SENSOR_PRESENCE_2 = "sensor.bedroom_presence"

# Test areas
TEST_AREA_LIVING_ROOM = "living_room"
TEST_AREA_BEDROOM = "bedroom"
TEST_AREA_KITCHEN = "kitchen"


def get_test_occupancy_areas_config() -> dict[str, dict]:
    """Get test areas configuration for occupancy testing."""
    return {
        TEST_AREA_LIVING_ROOM: {
            CONF_AREA_ID: TEST_AREA_LIVING_ROOM,
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: [TEST_BINARY_SENSOR_MOTION_1, TEST_BINARY_SENSOR_MOTION_2],
            CONF_SENSORS: [TEST_SENSOR_PRESENCE_1],
            "name": "Living Room",
        },
        TEST_AREA_BEDROOM: {
            CONF_AREA_ID: TEST_AREA_BEDROOM,
            CONF_AREA_ENABLED: True,
            CONF_BINARY_SENSORS: [TEST_BINARY_SENSOR_OCCUPANCY],
            CONF_SENSORS: [TEST_SENSOR_PRESENCE_2],
            "name": "Bedroom",
        },
        TEST_AREA_KITCHEN: {
            CONF_AREA_ID: TEST_AREA_KITCHEN,
            CONF_AREA_ENABLED: False,  # Disabled area
            CONF_BINARY_SENSORS: ["binary_sensor.kitchen_motion"],
            CONF_SENSORS: [],
            "name": "Kitchen",
        },
    }


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """Enable custom integrations for all tests."""
    pass


@pytest.fixture
async def setup_occupancy_entities(hass: HomeAssistant) -> None:
    """Set up test occupancy sensor entities."""
    # Set up binary sensors (all off initially)
    hass.states.async_set(
        TEST_BINARY_SENSOR_MOTION_1,
        STATE_OFF,
        {"friendly_name": "Living Room Motion", "device_class": "motion"},
    )
    hass.states.async_set(
        TEST_BINARY_SENSOR_MOTION_2,
        STATE_OFF,
        {"friendly_name": "Living Room Motion 2", "device_class": "motion"},
    )
    hass.states.async_set(
        TEST_BINARY_SENSOR_OCCUPANCY,
        STATE_OFF,
        {"friendly_name": "Bedroom Occupancy", "device_class": "occupancy"},
    )

    # Set up regular sensors with previous_valid_state attribute (all off initially)
    hass.states.async_set(
        TEST_SENSOR_PRESENCE_1,
        "0",  # Sensor state value (not used for occupancy)
        {
            "friendly_name": "Living Room Presence",
            "previous_valid_state": STATE_OFF,
        },
    )
    hass.states.async_set(
        TEST_SENSOR_PRESENCE_2,
        "0",
        {
            "friendly_name": "Bedroom Presence",
            "previous_valid_state": STATE_OFF,
        },
    )

    await hass.async_block_till_done()


class TestIsBinarySensorOccupied:
    """Tests for is_binary_sensor_occupied function."""

    def test_none_state_returns_false(self) -> None:
        """Test that None state returns False."""
        assert is_binary_sensor_occupied(None) is False

    def test_on_state_returns_true(self, hass: HomeAssistant) -> None:
        """Test that 'on' state returns True."""
        hass.states.async_set("binary_sensor.test", STATE_ON)
        state = hass.states.get("binary_sensor.test")
        assert is_binary_sensor_occupied(state) is True

    def test_off_state_returns_false(self, hass: HomeAssistant) -> None:
        """Test that 'off' state returns False."""
        hass.states.async_set("binary_sensor.test", STATE_OFF)
        state = hass.states.get("binary_sensor.test")
        assert is_binary_sensor_occupied(state) is False

    def test_unavailable_state_returns_false(self, hass: HomeAssistant) -> None:
        """Test that 'unavailable' state returns False."""
        hass.states.async_set("binary_sensor.test", STATE_UNAVAILABLE)
        state = hass.states.get("binary_sensor.test")
        assert is_binary_sensor_occupied(state) is False

    def test_unknown_state_returns_false(self, hass: HomeAssistant) -> None:
        """Test that 'unknown' state returns False."""
        hass.states.async_set("binary_sensor.test", STATE_UNKNOWN)
        state = hass.states.get("binary_sensor.test")
        assert is_binary_sensor_occupied(state) is False


class TestIsSensorOccupied:
    """Tests for is_sensor_occupied function."""

    def test_none_state_returns_false(self) -> None:
        """Test that None state returns False."""
        assert is_sensor_occupied(None) is False

    def test_previous_valid_state_on_returns_true(self, hass: HomeAssistant) -> None:
        """Test that previous_valid_state='on' returns True."""
        hass.states.async_set(
            "sensor.test",
            "50",
            {"previous_valid_state": STATE_ON},
        )
        state = hass.states.get("sensor.test")
        assert is_sensor_occupied(state) is True

    def test_previous_valid_state_off_returns_false(self, hass: HomeAssistant) -> None:
        """Test that previous_valid_state='off' returns False."""
        hass.states.async_set(
            "sensor.test",
            "50",
            {"previous_valid_state": STATE_OFF},
        )
        state = hass.states.get("sensor.test")
        assert is_sensor_occupied(state) is False

    def test_no_previous_valid_state_attribute_returns_false(
        self, hass: HomeAssistant
    ) -> None:
        """Test that missing previous_valid_state attribute returns False."""
        hass.states.async_set("sensor.test", "50", {})
        state = hass.states.get("sensor.test")
        assert is_sensor_occupied(state) is False

    def test_unavailable_state_returns_false(self, hass: HomeAssistant) -> None:
        """Test that 'unavailable' state returns False."""
        hass.states.async_set(
            "sensor.test",
            STATE_UNAVAILABLE,
            {"previous_valid_state": STATE_ON},
        )
        state = hass.states.get("sensor.test")
        assert is_sensor_occupied(state) is False

    def test_unknown_state_returns_false(self, hass: HomeAssistant) -> None:
        """Test that 'unknown' state returns False."""
        hass.states.async_set(
            "sensor.test",
            STATE_UNKNOWN,
            {"previous_valid_state": STATE_ON},
        )
        state = hass.states.get("sensor.test")
        assert is_sensor_occupied(state) is False


class TestGetSensorOccupancyState:
    """Tests for get_sensor_occupancy_state function."""

    def test_binary_sensor_on(self, hass: HomeAssistant) -> None:
        """Test binary_sensor domain with 'on' state."""
        hass.states.async_set("binary_sensor.test", STATE_ON)
        state = hass.states.get("binary_sensor.test")
        assert get_sensor_occupancy_state("binary_sensor.test", state) is True

    def test_binary_sensor_off(self, hass: HomeAssistant) -> None:
        """Test binary_sensor domain with 'off' state."""
        hass.states.async_set("binary_sensor.test", STATE_OFF)
        state = hass.states.get("binary_sensor.test")
        assert get_sensor_occupancy_state("binary_sensor.test", state) is False

    def test_sensor_with_previous_valid_state_on(self, hass: HomeAssistant) -> None:
        """Test sensor domain with previous_valid_state='on'."""
        hass.states.async_set(
            "sensor.test",
            "50",
            {"previous_valid_state": STATE_ON},
        )
        state = hass.states.get("sensor.test")
        assert get_sensor_occupancy_state("sensor.test", state) is True

    def test_sensor_with_previous_valid_state_off(self, hass: HomeAssistant) -> None:
        """Test sensor domain with previous_valid_state='off'."""
        hass.states.async_set(
            "sensor.test",
            "50",
            {"previous_valid_state": STATE_OFF},
        )
        state = hass.states.get("sensor.test")
        assert get_sensor_occupancy_state("sensor.test", state) is False

    def test_none_state_returns_false(self) -> None:
        """Test that None state returns False for any entity ID."""
        assert get_sensor_occupancy_state("binary_sensor.test", None) is False
        assert get_sensor_occupancy_state("sensor.test", None) is False


class TestAreaOccupancyState:
    """Tests for AreaOccupancyState dataclass."""

    def test_is_occupied_with_no_sensors(self) -> None:
        """Test is_occupied returns False when no sensors are occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.test"],
            sensors=["sensor.test"],
        )
        assert area.is_occupied is False

    def test_is_occupied_with_binary_sensor(self) -> None:
        """Test is_occupied returns True when a binary sensor is occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.test"],
            sensors=["sensor.test"],
        )
        area.occupied_binary_sensors = {"binary_sensor.test"}
        assert area.is_occupied is True

    def test_is_occupied_with_regular_sensor(self) -> None:
        """Test is_occupied returns True when a regular sensor is occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.test"],
            sensors=["sensor.test"],
        )
        area.occupied_sensors = {"sensor.test"}
        assert area.is_occupied is True

    def test_is_occupied_with_both_sensor_types(self) -> None:
        """Test is_occupied returns True when both sensor types are occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.test"],
            sensors=["sensor.test"],
        )
        area.occupied_binary_sensors = {"binary_sensor.test"}
        area.occupied_sensors = {"sensor.test"}
        assert area.is_occupied is True

    def test_all_sensors(self) -> None:
        """Test all_sensors returns combined list."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.a", "binary_sensor.b"],
            sensors=["sensor.c"],
        )
        assert area.all_sensors == [
            "binary_sensor.a",
            "binary_sensor.b",
            "sensor.c",
        ]

    def test_occupied_sensor_count(self) -> None:
        """Test occupied_sensor_count returns correct count."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.a", "binary_sensor.b"],
            sensors=["sensor.c", "sensor.d"],
        )
        area.occupied_binary_sensors = {"binary_sensor.a", "binary_sensor.b"}
        area.occupied_sensors = {"sensor.c"}
        assert area.occupied_sensor_count == 3

    def test_total_sensor_count(self) -> None:
        """Test total_sensor_count returns correct count."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
            binary_sensors=["binary_sensor.a", "binary_sensor.b"],
            sensors=["sensor.c"],
        )
        assert area.total_sensor_count == 3

    def test_get_occupancy_duration_when_not_occupied(self) -> None:
        """Test get_occupancy_duration returns None when not occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        assert area.get_occupancy_duration() is None

    def test_get_occupancy_duration_when_occupied(self) -> None:
        """Test get_occupancy_duration returns correct duration."""
        now = dt_util.utcnow()
        start = now - timedelta(minutes=5)

        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        area.occupied_binary_sensors = {"binary_sensor.test"}
        area.occupancy_start_time = start

        duration = area.get_occupancy_duration(now)
        assert duration is not None
        assert duration == timedelta(minutes=5)

    def test_get_occupancy_minutes(self) -> None:
        """Test get_occupancy_minutes returns correct minutes."""
        now = dt_util.utcnow()
        start = now - timedelta(minutes=7, seconds=30)

        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        area.occupied_binary_sensors = {"binary_sensor.test"}
        area.occupancy_start_time = start

        minutes = area.get_occupancy_minutes(now)
        assert minutes == 7.5

    def test_get_occupancy_minutes_when_not_occupied(self) -> None:
        """Test get_occupancy_minutes returns 0 when not occupied."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        assert area.get_occupancy_minutes() == 0.0


class TestRoomOccupancyTrackerInit:
    """Tests for RoomOccupancyTracker initialization."""

    async def test_init_with_default_min_occupancy(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker initializes with default min_occupancy_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        assert tracker.min_occupancy_minutes == DEFAULT_MIN_OCCUPANCY_MINUTES

    async def test_init_with_custom_min_occupancy(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker initializes with custom min_occupancy_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=10,
        )
        assert tracker.min_occupancy_minutes == 10

    async def test_min_occupancy_minutes_setter(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test min_occupancy_minutes can be updated."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        tracker.min_occupancy_minutes = 15
        assert tracker.min_occupancy_minutes == 15

    async def test_disabled_areas_are_not_tracked(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that disabled areas are not included in tracking."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )

        # Kitchen is disabled, should not be tracked
        assert TEST_AREA_KITCHEN not in tracker.areas
        # Enabled areas should be tracked
        assert TEST_AREA_LIVING_ROOM in tracker.areas
        assert TEST_AREA_BEDROOM in tracker.areas

    async def test_all_tracked_sensors(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test all_tracked_sensors returns all sensors from enabled areas."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )

        sensors = tracker.all_tracked_sensors
        assert TEST_BINARY_SENSOR_MOTION_1 in sensors
        assert TEST_BINARY_SENSOR_MOTION_2 in sensors
        assert TEST_BINARY_SENSOR_OCCUPANCY in sensors
        assert TEST_SENSOR_PRESENCE_1 in sensors
        assert TEST_SENSOR_PRESENCE_2 in sensors
        # Kitchen sensor should NOT be included (disabled)
        assert "binary_sensor.kitchen_motion" not in sensors


class TestRoomOccupancyTrackerSetupShutdown:
    """Tests for RoomOccupancyTracker setup and shutdown."""

    async def test_setup_subscribes_to_state_changes(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test setup subscribes to sensor state changes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        assert tracker._unsub_state_change is not None

        await tracker.async_shutdown()

    async def test_shutdown_unsubscribes(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test shutdown unsubscribes from state changes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()
        await tracker.async_shutdown()

        assert tracker._unsub_state_change is None

    async def test_setup_scans_initial_sensor_states(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test setup performs initial scan of sensor states."""
        # Set a sensor to occupied before setup
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # Living room should be occupied
        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area is not None
        assert area.is_occupied is True
        assert TEST_BINARY_SENSOR_MOTION_1 in area.occupied_binary_sensors

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerOccupancy:
    """Tests for RoomOccupancyTracker occupancy detection."""

    async def test_binary_sensor_on_makes_area_occupied(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test binary sensor turning on makes area occupied."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # Turn on motion sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area is not None
        assert area.is_occupied is True

        await tracker.async_shutdown()

    async def test_sensor_previous_valid_state_on_makes_area_occupied(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test sensor with previous_valid_state='on' makes area occupied."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # Update sensor with previous_valid_state = on
        hass.states.async_set(
            TEST_SENSOR_PRESENCE_1,
            "1",
            {
                "friendly_name": "Living Room Presence",
                "previous_valid_state": STATE_ON,
            },
        )
        await hass.async_block_till_done()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area is not None
        assert area.is_occupied is True

        await tracker.async_shutdown()

    async def test_multiple_sensors_or_logic(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that occupancy uses OR logic across multiple sensors."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # Turn on first motion sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area.is_occupied is True

        # Turn on second motion sensor too
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_2,
            STATE_ON,
            {"friendly_name": "Living Room Motion 2", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is True
        assert len(area.occupied_binary_sensors) == 2

        # Turn off first sensor - should still be occupied due to second
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is True
        assert len(area.occupied_binary_sensors) == 1
        assert TEST_BINARY_SENSOR_MOTION_2 in area.occupied_binary_sensors

        await tracker.async_shutdown()

    async def test_continuous_occupancy_with_sensor_handoff(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test continuous occupancy when sensors hand off to each other.

        Scenario: Sensor 1 on -> Sensor 2 on -> Sensor 1 off -> still occupied
        The occupancy start time should remain unchanged during the handoff.
        """
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Turn on first motion sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is True
        initial_start_time = area.occupancy_start_time
        assert initial_start_time is not None

        # Wait a moment, then turn on second sensor
        await asyncio.sleep(0.1)

        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_2,
            STATE_ON,
            {"friendly_name": "Living Room Motion 2", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Still occupied, start time unchanged
        assert area.is_occupied is True
        assert area.occupancy_start_time == initial_start_time

        # Turn off first sensor - second is still on
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Still occupied with same start time (continuous occupancy)
        assert area.is_occupied is True
        assert area.occupancy_start_time == initial_start_time

        await tracker.async_shutdown()

    async def test_all_sensors_off_clears_occupancy(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that occupancy is cleared when all sensors turn off."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Turn on sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is True
        assert area.occupancy_start_time is not None

        # Turn off sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is False
        assert area.occupancy_start_time is None
        assert area.is_active is False

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerActiveStatus:
    """Tests for RoomOccupancyTracker active status determination."""

    async def test_newly_occupied_area_is_not_active(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that a newly occupied area is not immediately active."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        # Turn on sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area.is_occupied is True
        assert area.is_active is False  # Not yet active (just started)

        await tracker.async_shutdown()

    async def test_area_becomes_active_after_threshold(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that an area becomes active after min_occupancy_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Manually set occupancy with start time in the past
        now = dt_util.utcnow()
        area.occupied_binary_sensors = {TEST_BINARY_SENSOR_MOTION_1}
        area.occupancy_start_time = now - timedelta(minutes=6)

        # Force update active status
        tracker.force_update_active_status()

        assert area.is_occupied is True
        assert area.is_active is True

        await tracker.async_shutdown()

    async def test_area_not_active_before_threshold(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that an area is not active before min_occupancy_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Manually set occupancy with start time less than threshold
        now = dt_util.utcnow()
        area.occupied_binary_sensors = {TEST_BINARY_SENSOR_MOTION_1}
        area.occupancy_start_time = now - timedelta(minutes=3)

        # Force update active status
        tracker.force_update_active_status()

        assert area.is_occupied is True
        assert area.is_active is False

        await tracker.async_shutdown()

    async def test_changing_min_occupancy_updates_active_status(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that changing min_occupancy_minutes re-evaluates active status."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=10,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Set occupancy with 7 minutes elapsed
        now = dt_util.utcnow()
        area.occupied_binary_sensors = {TEST_BINARY_SENSOR_MOTION_1}
        area.occupancy_start_time = now - timedelta(minutes=7)

        # Update active status - not active yet (7 < 10)
        tracker.force_update_active_status()
        assert area.is_active is False

        # Change threshold to 5 minutes
        tracker.min_occupancy_minutes = 5

        # Now should be active (7 >= 5)
        assert area.is_active is True

        await tracker.async_shutdown()

    async def test_init_with_default_grace_period(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker initializes with default grace_period_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        assert tracker.grace_period_minutes == DEFAULT_GRACE_PERIOD_MINUTES

    async def test_init_with_custom_grace_period(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker initializes with custom grace_period_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            grace_period_minutes=10,
        )
        assert tracker.grace_period_minutes == 10

    async def test_grace_period_minimum_enforced_on_init(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that grace_period_minutes is enforced to minimum of 2 on init."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            grace_period_minutes=1,
        )
        # Should be clamped to minimum of 2
        assert tracker.grace_period_minutes == 2

    async def test_grace_period_minimum_enforced_on_setter(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that grace_period_minutes setter enforces minimum of 2."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            grace_period_minutes=5,
        )
        tracker.grace_period_minutes = 1
        # Should be clamped to minimum of 2
        assert tracker.grace_period_minutes == 2

    async def test_grace_period_setter_updates_value(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test grace_period_minutes can be updated via setter."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            grace_period_minutes=5,
        )
        tracker.grace_period_minutes = 15
        assert tracker.grace_period_minutes == 15

    async def test_custom_grace_period_used_for_expiration(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that custom grace period is used for expiration calculation."""
        # Use a different grace period than min_occupancy
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
            grace_period_minutes=3,  # Different from min_occupancy
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Set up area as in grace period
        now = dt_util.utcnow()
        area.is_active = True
        area.was_active_before_unoccupied = True
        area.unoccupancy_start_time = now - timedelta(minutes=2)  # Less than grace period

        # Should still be active (only 2 min, grace period is 3)
        tracker.force_update_active_status()
        assert area.is_active is True
        assert area.is_in_grace_period is True

        # Now simulate being unoccupied for longer than grace period (3 min)
        area.unoccupancy_start_time = now - timedelta(minutes=4)
        tracker.force_update_active_status()

        # Should now be inactive (4 min > 3 min grace period)
        assert area.is_active is False
        assert area.is_in_grace_period is False

        await tracker.async_shutdown()

    async def test_grace_period_independent_of_min_occupancy(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that grace period and min_occupancy are truly independent."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=10,  # Long time to become active
            grace_period_minutes=2,     # Short grace period
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Set up area as in grace period
        now = dt_util.utcnow()
        area.is_active = True
        area.was_active_before_unoccupied = True
        area.unoccupancy_start_time = now - timedelta(minutes=2.5)

        # Force update - should expire based on grace period (2 min), not min_occupancy (10 min)
        tracker.force_update_active_status()
        assert area.is_active is False  # Expired after 2 min grace period
        assert area.is_in_grace_period is False

        await tracker.async_shutdown()

    async def test_active_enters_grace_period_when_unoccupied(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that an active area enters grace period when unoccupied."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # First turn the sensor ON to properly occupy the area
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Verify area is now occupied
        assert area.is_occupied is True

        # Set as active by back-dating the occupancy start time
        now = dt_util.utcnow()
        area.occupancy_start_time = now - timedelta(minutes=10)
        area.is_active = True

        # Turn off the sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Area should be unoccupied but still active (in grace period)
        assert area.is_occupied is False
        assert area.is_active is True
        assert area.is_in_grace_period is True
        assert area.was_active_before_unoccupied is True
        assert area.unoccupancy_start_time is not None

        await tracker.async_shutdown()

    async def test_grace_period_expires_after_threshold(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that grace period expires after min_occupancy_minutes of unoccupancy."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Set up area as in grace period with back-dated unoccupancy
        now = dt_util.utcnow()
        area.is_active = True
        area.was_active_before_unoccupied = True
        area.unoccupancy_start_time = now - timedelta(minutes=6)  # More than threshold

        # Force update active status
        tracker.force_update_active_status()

        # Grace period should have expired
        assert area.is_active is False
        assert area.is_in_grace_period is False
        assert area.was_active_before_unoccupied is False
        assert area.unoccupancy_start_time is None

        await tracker.async_shutdown()

    async def test_reoccupancy_during_grace_period_remains_active(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that re-occupancy during grace period maintains active status."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # First turn the sensor ON to properly occupy the area
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Set as active by back-dating the occupancy start time
        now = dt_util.utcnow()
        area.occupancy_start_time = now - timedelta(minutes=10)
        area.is_active = True

        # Turn off the sensor (enters grace period)
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_in_grace_period is True
        assert area.is_active is True

        # Turn on the sensor again (re-occupy during grace period)
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Should be occupied and still active
        assert area.is_occupied is True
        assert area.is_active is True
        # Grace period state should be cleared
        assert area.is_in_grace_period is False
        assert area.was_active_before_unoccupied is False
        assert area.unoccupancy_start_time is None

        await tracker.async_shutdown()

    async def test_non_active_area_no_grace_period_when_unoccupied(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that a non-active area does not enter grace period when unoccupied."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Turn on sensor (occupied but not yet active)
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert area.is_occupied is True
        assert area.is_active is False

        # Turn off sensor
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Should be unoccupied and not in grace period
        assert area.is_occupied is False
        assert area.is_active is False
        assert area.is_in_grace_period is False
        assert area.was_active_before_unoccupied is False

        await tracker.async_shutdown()

    async def test_grace_period_not_expired_before_threshold(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that grace period does not expire before min_occupancy_minutes."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)

        # Set up area as in grace period with recent unoccupancy
        now = dt_util.utcnow()
        area.is_active = True
        area.was_active_before_unoccupied = True
        area.unoccupancy_start_time = now - timedelta(minutes=3)  # Less than threshold

        # Force update active status
        tracker.force_update_active_status()

        # Grace period should still be active
        assert area.is_active is True
        assert area.is_in_grace_period is True

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerProperties:
    """Tests for RoomOccupancyTracker aggregate properties."""

    async def test_occupied_areas_property(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test occupied_areas returns correct areas."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # Initially no areas occupied
        assert len(tracker.occupied_areas) == 0

        # Occupy living room
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert len(tracker.occupied_areas) == 1
        assert tracker.occupied_areas[0].area_id == TEST_AREA_LIVING_ROOM

        # Occupy bedroom too
        hass.states.async_set(
            TEST_BINARY_SENSOR_OCCUPANCY,
            STATE_ON,
            {"friendly_name": "Bedroom Occupancy", "device_class": "occupancy"},
        )
        await hass.async_block_till_done()

        assert len(tracker.occupied_areas) == 2

        await tracker.async_shutdown()

    async def test_active_areas_property(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test active_areas returns correct areas."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        # Initially no areas active
        assert len(tracker.active_areas) == 0

        # Set living room as occupied and active
        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        now = dt_util.utcnow()
        area.occupied_binary_sensors = {TEST_BINARY_SENSOR_MOTION_1}
        area.occupancy_start_time = now - timedelta(minutes=10)
        tracker.force_update_active_status()

        assert len(tracker.active_areas) == 1
        assert tracker.active_areas[0].area_id == TEST_AREA_LIVING_ROOM

        await tracker.async_shutdown()

    async def test_any_area_occupied_property(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test any_area_occupied property."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        assert tracker.any_area_occupied is False

        # Occupy an area
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert tracker.any_area_occupied is True

        await tracker.async_shutdown()

    async def test_any_area_active_property(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test any_area_active property."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        assert tracker.any_area_active is False

        # Set an area as active
        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        now = dt_util.utcnow()
        area.occupied_binary_sensors = {TEST_BINARY_SENSOR_MOTION_1}
        area.occupancy_start_time = now - timedelta(minutes=10)
        tracker.force_update_active_status()

        assert tracker.any_area_active is True

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerCallbacks:
    """Tests for RoomOccupancyTracker update callbacks."""

    async def test_register_update_callback(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test registering an update callback."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        callback_called = []

        def my_callback():
            callback_called.append(True)

        unregister = tracker.register_update_callback(my_callback)

        # Trigger occupancy change
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert len(callback_called) == 1

        # Unregister and verify no more calls
        unregister()

        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_OFF,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert len(callback_called) == 1  # Still just 1

        await tracker.async_shutdown()

    async def test_multiple_callbacks(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test multiple callbacks are all called."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        callback1_count = []
        callback2_count = []

        def callback1():
            callback1_count.append(1)

        def callback2():
            callback2_count.append(1)

        tracker.register_update_callback(callback1)
        tracker.register_update_callback(callback2)

        # Trigger occupancy change
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        assert len(callback1_count) == 1
        assert len(callback2_count) == 1

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerConfigUpdate:
    """Tests for RoomOccupancyTracker configuration updates."""

    async def test_update_config_rebuilds_tracking(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test that update_config rebuilds area tracking."""
        initial_config = get_test_occupancy_areas_config()
        tracker = RoomOccupancyTracker(hass, initial_config)
        await tracker.async_setup()

        # Initially has living room and bedroom
        assert TEST_AREA_LIVING_ROOM in tracker.areas
        assert TEST_AREA_BEDROOM in tracker.areas

        # Update config to disable bedroom
        new_config = get_test_occupancy_areas_config()
        new_config[TEST_AREA_BEDROOM][CONF_AREA_ENABLED] = False

        tracker.update_config(new_config)

        # Bedroom should no longer be tracked
        assert TEST_AREA_LIVING_ROOM in tracker.areas
        assert TEST_AREA_BEDROOM not in tracker.areas

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerSummary:
    """Tests for RoomOccupancyTracker get_summary method."""

    async def test_get_summary(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test get_summary returns correct data."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            min_occupancy_minutes=5,
        )
        await tracker.async_setup()

        # Set up some occupancy
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        summary = tracker.get_summary()

        assert summary["total_areas"] == 2  # Living room and bedroom (kitchen disabled)
        assert summary["occupied_areas"] == 1
        assert summary["active_areas"] == 0  # Not enough time
        assert summary["min_occupancy_minutes"] == 5

        assert TEST_AREA_LIVING_ROOM in summary["areas"]
        assert summary["areas"][TEST_AREA_LIVING_ROOM]["is_occupied"] is True
        assert summary["areas"][TEST_AREA_LIVING_ROOM]["is_active"] is False

        await tracker.async_shutdown()


class TestRoomOccupancyTrackerEdgeCases:
    """Tests for edge cases and error handling."""

    async def test_sensor_state_unavailable(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test handling of unavailable sensor state."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )
        await tracker.async_setup()

        # First make area occupied
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area.is_occupied is True

        # Set sensor to unavailable
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_UNAVAILABLE,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Should no longer count as occupied
        assert TEST_BINARY_SENSOR_MOTION_1 not in area.occupied_binary_sensors

        await tracker.async_shutdown()

    async def test_get_area_returns_none_for_unknown_area(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test get_area returns None for unknown area ID."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )

        assert tracker.get_area("nonexistent_area") is None

    async def test_empty_areas_config(self, hass: HomeAssistant) -> None:
        """Test tracker handles empty areas config."""
        tracker = RoomOccupancyTracker(hass, {})

        assert len(tracker.areas) == 0
        assert len(tracker.all_tracked_sensors) == 0
        assert tracker.any_area_occupied is False
        assert tracker.any_area_active is False

    async def test_area_with_no_occupancy_sensors(
        self, hass: HomeAssistant
    ) -> None:
        """Test area with no sensors is not tracked."""
        config = {
            "empty_area": {
                CONF_AREA_ID: "empty_area",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_SENSORS: [],
                "name": "Empty Area",
            }
        }

        tracker = RoomOccupancyTracker(hass, config)

        # Area should not be tracked (no sensors)
        assert "empty_area" not in tracker.areas


class TestAreaOccupancyStateSerialization:
    """Tests for AreaOccupancyState serialization/deserialization."""

    def test_to_storage_dict_empty_state(self) -> None:
        """Test serialization of empty state."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )

        result = area.to_storage_dict()

        assert result["area_id"] == "test_area"
        assert result["is_active"] is False
        assert result["occupancy_start_time"] is None
        assert result["was_active_before_unoccupied"] is False
        assert result["unoccupancy_start_time"] is None

    def test_to_storage_dict_active_state(self) -> None:
        """Test serialization of active state with timestamps."""
        now = dt_util.utcnow()
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        area.is_active = True
        area.occupancy_start_time = now

        result = area.to_storage_dict()

        assert result["is_active"] is True
        assert result["occupancy_start_time"] == now.isoformat()

    def test_to_storage_dict_grace_period_state(self) -> None:
        """Test serialization of grace period state."""
        now = dt_util.utcnow()
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )
        area.is_active = True
        area.was_active_before_unoccupied = True
        area.unoccupancy_start_time = now

        result = area.to_storage_dict()

        assert result["was_active_before_unoccupied"] is True
        assert result["unoccupancy_start_time"] == now.isoformat()

    def test_restore_from_storage_empty_data(self) -> None:
        """Test restoration with empty data does not change state."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )

        area.restore_from_storage({})

        assert area.is_active is False
        assert area.occupancy_start_time is None

    def test_restore_from_storage_active_state(self) -> None:
        """Test restoration of active state."""
        now = dt_util.utcnow()
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )

        area.restore_from_storage({
            "is_active": True,
            "occupancy_start_time": now.isoformat(),
        })

        assert area.is_active is True
        assert area.occupancy_start_time == now

    def test_restore_from_storage_grace_period_state(self) -> None:
        """Test restoration of grace period state."""
        now = dt_util.utcnow()
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )

        area.restore_from_storage({
            "was_active_before_unoccupied": True,
            "unoccupancy_start_time": now.isoformat(),
        })

        assert area.was_active_before_unoccupied is True
        assert area.unoccupancy_start_time == now

    def test_restore_from_storage_invalid_timestamp(self) -> None:
        """Test restoration handles invalid timestamps gracefully."""
        area = AreaOccupancyState(
            area_id="test_area",
            area_name="Test Area",
        )

        area.restore_from_storage({
            "is_active": True,
            "occupancy_start_time": "invalid-timestamp",
        })

        # Should restore is_active but skip invalid timestamp
        assert area.is_active is True
        assert area.occupancy_start_time is None


class TestRoomOccupancyTrackerPersistence:
    """Tests for RoomOccupancyTracker state persistence."""

    async def test_tracker_with_entry_id_has_store(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker initializes store when entry_id is provided."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_id",
        )

        assert tracker._store is not None
        assert "test_entry_id" in tracker._store.key

    async def test_tracker_without_entry_id_no_store(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test tracker has no store when entry_id is not provided."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
        )

        assert tracker._store is None

    async def test_save_state_creates_data(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test saving state creates properly structured data."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_id",
        )

        # Make an area active
        now = dt_util.utcnow()
        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        area.is_active = True
        area.occupancy_start_time = now - timedelta(minutes=10)

        # Save state
        await tracker._async_save_state()

        # Load and verify
        stored_data = await tracker._store.async_load()
        assert stored_data is not None
        assert "areas" in stored_data
        assert TEST_AREA_LIVING_ROOM in stored_data["areas"]
        assert stored_data["areas"][TEST_AREA_LIVING_ROOM]["is_active"] is True

    async def test_restore_state_loads_data(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test restoring state loads and applies data correctly."""
        now = dt_util.utcnow()
        occupancy_start = now - timedelta(minutes=10)

        # Create first tracker and save state
        tracker1 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_id",
        )

        area1 = tracker1.get_area(TEST_AREA_LIVING_ROOM)
        area1.is_active = True
        area1.occupancy_start_time = occupancy_start

        await tracker1._async_save_state()

        # Create second tracker (simulating restart) and restore
        tracker2 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_id",
        )

        await tracker2._async_restore_state()

        # Verify state was restored
        area2 = tracker2.get_area(TEST_AREA_LIVING_ROOM)
        assert area2.is_active is True
        assert area2.occupancy_start_time == occupancy_start

    async def test_restore_state_no_data(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test restoring state with no saved data doesn't crash."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="unique_entry_no_data",
        )

        # Should not raise
        await tracker._async_restore_state()

        # State should remain at defaults
        area = tracker.get_area(TEST_AREA_LIVING_ROOM)
        assert area.is_active is False

    async def test_setup_restores_state(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test async_setup restores state before scanning sensors."""
        now = dt_util.utcnow()
        occupancy_start = now - timedelta(minutes=10)

        # Turn on sensor to establish occupancy
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        # Create first tracker, make area active, save and shutdown
        tracker1 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_persist",
        )
        await tracker1.async_setup()

        area1 = tracker1.get_area(TEST_AREA_LIVING_ROOM)
        # Override the occupancy_start_time to simulate longer occupancy
        area1.occupancy_start_time = occupancy_start
        area1.is_active = True  # Force active for test

        await tracker1.async_shutdown()  # This saves state

        # Create new tracker and setup (simulating restart)
        # Sensor is still on, so occupancy will be maintained
        tracker2 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_persist",
        )
        await tracker2.async_setup()

        # State should be restored
        area2 = tracker2.get_area(TEST_AREA_LIVING_ROOM)
        assert area2.is_active is True
        assert area2.occupancy_start_time == occupancy_start

        await tracker2.async_shutdown()

    async def test_shutdown_saves_state(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test async_shutdown saves state that can be restored."""
        # Turn on sensor to establish occupancy
        hass.states.async_set(
            TEST_BINARY_SENSOR_MOTION_1,
            STATE_ON,
            {"friendly_name": "Living Room Motion", "device_class": "motion"},
        )
        await hass.async_block_till_done()

        tracker1 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_shutdown",
        )
        await tracker1.async_setup()

        # Make area active with specific timestamp
        now = dt_util.utcnow()
        original_start_time = now - timedelta(minutes=5)
        area1 = tracker1.get_area(TEST_AREA_LIVING_ROOM)
        area1.is_active = True
        area1.occupancy_start_time = original_start_time

        # Shutdown (should save)
        await tracker1.async_shutdown()

        # Create new tracker and verify state is restored (proving save worked)
        tracker2 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_shutdown",
        )
        await tracker2.async_setup()

        area2 = tracker2.get_area(TEST_AREA_LIVING_ROOM)
        # is_active should be preserved
        assert area2.is_active is True
        # occupancy_start_time should be preserved from restore
        assert area2.occupancy_start_time == original_start_time

        await tracker2.async_shutdown()

    async def test_restore_ignores_unknown_areas(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test restoring state ignores areas that no longer exist in config."""
        tracker = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_unknown",
        )

        # Manually save data with an unknown area
        await tracker._store.async_save({
            "version": 1,
            "saved_at": dt_util.utcnow().isoformat(),
            "areas": {
                "unknown_area": {"is_active": True},
                TEST_AREA_LIVING_ROOM: {"is_active": True},
            },
        })

        # Restore - should not crash
        await tracker._async_restore_state()

        # Known area should be restored
        assert tracker.get_area(TEST_AREA_LIVING_ROOM).is_active is True
        # Unknown area should not exist
        assert tracker.get_area("unknown_area") is None

    async def test_persist_grace_period_state(
        self, hass: HomeAssistant, setup_occupancy_entities
    ) -> None:
        """Test grace period state is persisted and restored."""
        now = dt_util.utcnow()
        unoccupancy_start = now - timedelta(minutes=2)

        # Create tracker and set grace period state
        tracker1 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_grace",
        )
        await tracker1.async_setup()

        area1 = tracker1.get_area(TEST_AREA_LIVING_ROOM)
        area1.is_active = True
        area1.was_active_before_unoccupied = True
        area1.unoccupancy_start_time = unoccupancy_start

        await tracker1.async_shutdown()

        # Create new tracker
        tracker2 = RoomOccupancyTracker(
            hass,
            get_test_occupancy_areas_config(),
            entry_id="test_entry_grace",
        )
        await tracker2.async_setup()

        # Grace period state should be restored
        area2 = tracker2.get_area(TEST_AREA_LIVING_ROOM)
        assert area2.is_active is True
        assert area2.was_active_before_unoccupied is True
        assert area2.unoccupancy_start_time == unoccupancy_start

        await tracker2.async_shutdown()
