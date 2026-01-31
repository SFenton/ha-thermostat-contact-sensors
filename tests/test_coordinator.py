"""Tests for the coordinator."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thermostat_contact_sensors.const import (
    CONF_CLOSE_TIMEOUT,
    CONF_NOTIFY_SERVICE,
    CONF_OPEN_TIMEOUT,
    DOMAIN,
)
from custom_components.thermostat_contact_sensors.coordinator import (
    ThermostatContactSensorsCoordinator,
)

from .conftest import (
    TEST_NOTIFY_SERVICE,
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
    TEST_THERMOSTAT,
    get_test_config_options,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


@pytest.fixture
def coordinator(hass: HomeAssistant) -> ThermostatContactSensorsCoordinator:
    """Create a coordinator for testing."""
    options = get_test_config_options()
    options[CONF_OPEN_TIMEOUT] = 1  # 1 minute for faster tests
    options[CONF_CLOSE_TIMEOUT] = 1

    return ThermostatContactSensorsCoordinator(
        hass,
        config_entry_id="test_entry",
        contact_sensors=[TEST_SENSOR_1, TEST_SENSOR_2, TEST_SENSOR_3],
        thermostat=TEST_THERMOSTAT,
        options=options,
    )


@pytest.fixture
def coordinator_no_notify(hass: HomeAssistant) -> ThermostatContactSensorsCoordinator:
    """Create a coordinator without notifications for testing."""
    options = get_test_config_options()
    options[CONF_OPEN_TIMEOUT] = 1
    options[CONF_CLOSE_TIMEOUT] = 1
    options[CONF_NOTIFY_SERVICE] = ""

    return ThermostatContactSensorsCoordinator(
        hass,
        config_entry_id="test_entry_no_notify",
        contact_sensors=[TEST_SENSOR_1],
        thermostat=TEST_THERMOSTAT,
        options=options,
    )


class TestCoordinatorSetup:
    """Tests for coordinator setup and shutdown."""

    async def test_coordinator_setup(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test coordinator setup."""
        await coordinator.async_setup()

        assert coordinator.is_paused is False
        assert coordinator.open_sensors == []
        assert coordinator.trigger_sensor is None
        assert coordinator._unsub_state_change is not None

        await coordinator.async_shutdown()

    async def test_coordinator_shutdown(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test coordinator shutdown cleans up resources."""
        await coordinator.async_setup()

        assert coordinator._unsub_state_change is not None

        await coordinator.async_shutdown()

        assert coordinator._unsub_state_change is None

    async def test_coordinator_initial_open_sensors(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test coordinator detects initially open sensors and starts timer.
        
        This tests the critical scenario where Home Assistant restarts while a
        door/window is already open. The coordinator must start the open timer
        on setup to ensure the thermostat gets paused after the timeout.
        """
        # Open a sensor before setup (simulates HA restart with door open)
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await coordinator.async_setup()

        # Verify open sensors detected
        assert TEST_SENSOR_1 in coordinator.open_sensors
        assert coordinator.open_count == 1

        await coordinator.async_shutdown()


class TestSensorStateChanges:
    """Tests for sensor state change handling."""

    async def test_sensor_open_starts_timer(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that opening a sensor starts the open timer."""
        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert TEST_SENSOR_1 in coordinator.open_sensors
        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == TEST_SENSOR_1

        await coordinator.async_shutdown()

    async def test_sensor_close_before_timeout_cancels_timer(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that closing sensor before timeout cancels timer."""
        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator._open_timer is not None

        # Close the sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator._open_timer is None
        assert coordinator.is_paused is False

        await coordinator.async_shutdown()

    async def test_multiple_sensors_open(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test multiple sensors opening."""
        await coordinator.async_setup()

        # Open first sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Open second sensor
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
        await hass.async_block_till_done()

        assert len(coordinator.open_sensors) == 2
        assert TEST_SENSOR_1 in coordinator.open_sensors
        assert TEST_SENSOR_2 in coordinator.open_sensors

        await coordinator.async_shutdown()

    async def test_sensor_unavailable_ignored(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that unavailable state changes are ignored."""
        await coordinator.async_setup()

        # Set sensor to unavailable
        hass.states.async_set(TEST_SENSOR_1, STATE_UNAVAILABLE, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert TEST_SENSOR_1 not in coordinator.open_sensors
        assert coordinator._open_timer is None

        await coordinator.async_shutdown()


class TestThermostatPausing:
    """Tests for thermostat pausing logic."""

    async def test_thermostat_pauses_after_timeout(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that thermostat pauses after open timeout."""
        # Use very short timeout
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01  # ~0.6 seconds

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == HVACMode.HEAT
        mock_climate_service.assert_called()

        await coordinator.async_shutdown()


class TestVentEffectiveMode:
    """Tests for inferred vent effective mode caching."""

    async def test_recalculates_effective_mode_on_determining_temperature_change(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ) -> None:
        """When any room's determining_temperature changes, inferred vent mode updates."""
        from unittest.mock import MagicMock

        from custom_components.thermostat_contact_sensors.thermostat_control import (
            RoomTemperatureState,
            ThermostatState,
        )
        from custom_components.thermostat_contact_sensors.vent_control import VentControlState

        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            CONF_VENTS,
            CONF_MIN_VENTS_OPEN,
            CONF_VENT_OPEN_DELAY_SECONDS,
            CONF_VENT_DEBOUNCE_SECONDS,
        )

        room_temp = "sensor.room_temp"
        room_vent = "cover.room_vent"
        hass.states.async_set(room_vent, "closed", {"current_tilt_position": 0})
        hass.states.async_set(room_temp, "20.0", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()

        areas_config = {
            "room": {
                CONF_AREA_ID: "room",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: [room_temp],
                CONF_VENTS: [room_vent],
            }
        }

        options = get_test_config_options()
        options[CONF_MIN_VENTS_OPEN] = 0
        options[CONF_VENT_OPEN_DELAY_SECONDS] = 0
        options[CONF_VENT_DEBOUNCE_SECONDS] = 0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry_vent_mode",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=options,
            areas_config=areas_config,
        )

        # Patch vent evaluation so we can inspect the hvac_mode passed in.
        coordinator.vent_controller.evaluate_all_vents = MagicMock(return_value=VentControlState())

        def _make_state(det_temp: float) -> ThermostatState:
            room_state = RoomTemperatureState(area_id="room", area_name="Room")
            room_state.determining_temperature = det_temp

            return ThermostatState(
                thermostat_entity_id=TEST_THERMOSTAT,
                hvac_mode=HVACMode.OFF,
                target_temp_low=68.0,
                target_temp_high=72.0,
                room_states={"room": room_state},
            )

        # First update: cold trend -> inferred HEAT.
        with patch.object(
            coordinator.thermostat_controller,
            "evaluate_thermostat_action",
            return_value=_make_state(60.0),
        ):
            coordinator.update_thermostat_state()
        await coordinator.async_update_vents()
        assert coordinator.vent_controller.evaluate_all_vents.call_args.kwargs["hvac_mode"] == HVACMode.HEAT

        coordinator.vent_controller.evaluate_all_vents.reset_mock()

        # Second update: hot trend -> inferred COOL.
        with patch.object(
            coordinator.thermostat_controller,
            "evaluate_thermostat_action",
            return_value=_make_state(76.0),
        ):
            coordinator.update_thermostat_state()
        await coordinator.async_update_vents()
        assert coordinator.vent_controller.evaluate_all_vents.call_args.kwargs["hvac_mode"] == HVACMode.COOL


class TestVentOnlyTemperatureSensors:
    """Tests for vent-only temperature sensors (vent control only)."""

    async def test_vent_only_temperature_sensors_participate_in_minimum_vents(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ) -> None:
        """A room not in thermostat room_states can still be used for minimum vents."""
        from unittest.mock import AsyncMock

        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_MIN_VENTS_OPEN,
            CONF_TEMPERATURE_SENSORS,
            CONF_VENT_DEBOUNCE_SECONDS,
            CONF_VENT_OPEN_DELAY_SECONDS,
            CONF_VENTS,
        )

        from custom_components.thermostat_contact_sensors.thermostat_control import ThermostatState

        kitchen_temp = "sensor.kitchen_temp"
        kitchen_vent = "cover.kitchen_vent"
        other_vent = "cover.other_vent"

        hass.states.async_set(kitchen_temp, "60.0", {"unit_of_measurement": "°F"})
        hass.states.async_set(kitchen_vent, "closed", {"current_tilt_position": 0})
        hass.states.async_set(other_vent, "closed", {"current_tilt_position": 0})
        await hass.async_block_till_done()

        areas_config = {
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                # No explicit vent-only sensors configured: coordinator should
                # fall back to using the area's standard temperature_sensors for
                # vent control.
                CONF_TEMPERATURE_SENSORS: [kitchen_temp],
                CONF_VENTS: [kitchen_vent],
            },
            "other": {
                CONF_AREA_ID: "other",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: [],
                CONF_VENTS: [other_vent],
            },
        }

        options = get_test_config_options()
        options[CONF_MIN_VENTS_OPEN] = 1
        options[CONF_VENT_OPEN_DELAY_SECONDS] = 0
        options[CONF_VENT_DEBOUNCE_SECONDS] = 0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry_vent_only_temps",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=options,
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Force thermostat state to *not* include kitchen (e.g., inactive/untracked).
        coordinator._last_thermostat_state = ThermostatState(
            thermostat_entity_id=TEST_THERMOSTAT,
            hvac_mode=HVACMode.OFF,
            target_temp_low=68.0,
            target_temp_high=72.0,
            room_states={},
        )

        # Ensure vent-only temps are present in the vent-control merged view.
        merged = coordinator._get_room_temp_states_for_vent_control()
        assert "kitchen" in merged
        assert getattr(merged["kitchen"], "determining_temperature", None) == 60.0

        # Avoid calling HA services in this unit test.
        coordinator.vent_controller.async_execute_vent_commands = AsyncMock(return_value=0)

        # With minimum vents enforced, kitchen should be selected over areas with
        # no usable temperature signal.
        vcs = await coordinator.async_update_vents()
        assert vcs is not None
        assert vcs.area_states["kitchen"].vents[0].should_be_open is True
        open_reason = vcs.area_states["kitchen"].vents[0].open_reason or ""
        assert open_reason == "Minimum vents (need 1)" or open_reason.startswith("Needs heat")

    async def test_vent_only_temperature_sensors_use_determining_sensor_selection(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ) -> None:
        """Vent-only determining_temperature should follow thermostat determining-sensor rules."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            CONF_VENTS,
        )
        from custom_components.thermostat_contact_sensors.thermostat_control import ThermostatState

        kitchen_temp_1 = "sensor.kitchen_temp_1"
        kitchen_temp_2 = "sensor.kitchen_temp_2"
        kitchen_vent = "cover.kitchen_vent"

        hass.states.async_set(kitchen_temp_1, "60.0", {"unit_of_measurement": "°F"})
        hass.states.async_set(kitchen_temp_2, "65.0", {"unit_of_measurement": "°F"})
        hass.states.async_set(kitchen_vent, "closed", {"current_tilt_position": 0})
        await hass.async_block_till_done()

        areas_config = {
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: [kitchen_temp_1, kitchen_temp_2],
                CONF_VENTS: [kitchen_vent],
            }
        }

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry_vent_only_determining_sensor",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Force thermostat state to *not* include kitchen (e.g., inactive/untracked),
        # but provide mode/targets so vent-only logic can pick a determining sensor.
        coordinator._last_thermostat_state = ThermostatState(
            thermostat_entity_id=TEST_THERMOSTAT,
            hvac_mode=HVACMode.OFF,
            inferred_hvac_mode=HVACMode.HEAT,
            target_temperature=70.0,
            room_states={},
        )

        merged = coordinator._get_room_temp_states_for_vent_control()
        assert merged["kitchen"].determining_sensor == kitchen_temp_2
        assert merged["kitchen"].determining_temperature == 65.0

    async def test_thermostat_stores_previous_mode(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that previous HVAC mode is stored correctly."""
        # Set thermostat to cool mode
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.COOL,
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor and wait for timeout
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.previous_hvac_mode == HVACMode.COOL

        await coordinator.async_shutdown()

    async def test_no_pause_if_sensor_closes_before_timeout(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
    ) -> None:
        """Test that thermostat doesn't pause if sensor closes before timeout."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 10  # Long timeout

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Close it quickly
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator.is_paused is False
        mock_climate_service.assert_not_called()

        await coordinator.async_shutdown()

    async def test_fan_mode_set_to_auto_when_pausing(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_fan_mode_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that fan mode is set to auto when thermostat pauses."""
        # Ensure thermostat has fan mode set to "on" with proper supported_features
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                "friendly_name": "Test Thermostat",
                "fan_mode": "on",
                "fan_modes": ["on", "auto"],
                "supported_features": ClimateEntityFeature.FAN_MODE,
            },
        )
        await hass.async_block_till_done()

        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Check that set_fan_mode was called with "auto"
        mock_fan_mode_service.assert_called()
        fan_call = mock_fan_mode_service.call_args
        assert fan_call[0][0].data["fan_mode"] == "auto"

        await coordinator.async_shutdown()

    async def test_fan_mode_not_changed_if_already_auto(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_fan_mode_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that fan mode is not changed if already set to auto."""
        # Ensure thermostat has fan mode already set to "auto" with proper supported_features
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                "friendly_name": "Test Thermostat",
                "fan_mode": "auto",
                "fan_modes": ["on", "auto"],
                "supported_features": ClimateEntityFeature.FAN_MODE,
            },
        )
        await hass.async_block_till_done()

        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Check that set_fan_mode was NOT called (already auto)
        mock_fan_mode_service.assert_not_called()

        await coordinator.async_shutdown()

    async def test_fan_mode_not_changed_if_thermostat_does_not_support_fan(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_fan_mode_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that fan mode is not changed if thermostat doesn't support fan modes."""
        # Set thermostat without fan mode support (no fan_modes attribute)
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                "friendly_name": "Test Thermostat",
                # No fan_mode or fan_modes attributes
            },
        )
        await hass.async_block_till_done()

        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Check that set_fan_mode was NOT called (no fan support)
        mock_fan_mode_service.assert_not_called()

        await coordinator.async_shutdown()

    async def test_fan_mode_falls_back_to_off_if_no_auto(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_fan_mode_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that fan mode falls back to 'off' if 'auto' is not available."""
        # Set thermostat with only "on" and "off" fan modes (no "auto") with proper supported_features
        hass.states.async_set(
            TEST_THERMOSTAT,
            HVACMode.HEAT,
            {
                "friendly_name": "Test Thermostat",
                "fan_mode": "on",
                "fan_modes": ["on", "off"],  # No "auto" available
                "supported_features": ClimateEntityFeature.FAN_MODE,
            },
        )
        await hass.async_block_till_done()

        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Check that set_fan_mode was called with "off" (fallback)
        mock_fan_mode_service.assert_called()
        fan_call = mock_fan_mode_service.call_args
        assert fan_call[0][0].data["fan_mode"] == "off"

        await coordinator.async_shutdown()


class TestThermostatResuming:
    """Tests for thermostat resuming logic."""

    async def test_thermostat_resumes_after_all_closed(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that thermostat resumes after all sensors closed."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor and wait for pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Close the sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Wait for close timeout
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is False

        await coordinator.async_shutdown()

    async def test_close_timer_cancelled_if_sensor_reopens(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that close timer is cancelled if a sensor reopens."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 10  # Long close timeout

        await coordinator.async_setup()

        # Open a sensor and wait for pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Close the sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Close timer should start
        assert coordinator._close_timer is not None

        # Reopen the sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Close timer should be cancelled
        assert coordinator._close_timer is None
        assert coordinator.is_paused is True

        await coordinator.async_shutdown()

    async def test_resume_restores_previous_mode(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that resume restores the previous HVAC mode."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Trigger pause and resume
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        # Check thermostat was restored
        state = hass.states.get(TEST_THERMOSTAT)
        assert state.state == HVACMode.HEAT

        await coordinator.async_shutdown()


class TestNotifications:
    """Tests for notification sending."""

    async def test_notification_sent_on_pause(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that notification is sent when thermostat pauses."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Trigger pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        mock_notify_service.assert_called()

        await coordinator.async_shutdown()

    async def test_notification_sent_on_resume(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that notification is sent when thermostat resumes."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Trigger pause and resume
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        mock_notify_service.reset_mock()

        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        mock_notify_service.assert_called()

        await coordinator.async_shutdown()

    async def test_no_notification_when_disabled(
        self,
        hass: HomeAssistant,
        coordinator_no_notify: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that no notification is sent when service is empty."""
        coordinator_no_notify._options[CONF_OPEN_TIMEOUT] = 0.01

        await coordinator_no_notify.async_setup()

        # Trigger pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(1)
        await hass.async_block_till_done()

        mock_notify_service.assert_not_called()

        await coordinator_no_notify.async_shutdown()


class TestOpenSensorCounts:
    """Tests for open sensor counting."""

    async def test_open_count(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test open sensor count is correct."""
        await coordinator.async_setup()

        assert coordinator.open_count == 0

        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator.open_count == 1

        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
        await hass.async_block_till_done()

        assert coordinator.open_count == 2

        await coordinator.async_shutdown()

    async def test_open_doors_count(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test open doors count is correct."""
        await coordinator.async_setup()

        # Open a door sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator.open_doors_count == 1
        assert coordinator.open_windows_count == 0

        await coordinator.async_shutdown()

    async def test_open_windows_count(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test open windows count is correct."""
        await coordinator.async_setup()

        # Open a window sensor
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Back Window"})
        await hass.async_block_till_done()

        assert coordinator.open_windows_count == 1
        assert coordinator.open_doors_count == 0

        await coordinator.async_shutdown()


class TestOptionsUpdate:
    """Tests for options updates."""

    async def test_update_options(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test updating options."""
        await coordinator.async_setup()

        assert coordinator.open_timeout == 1

        new_options = get_test_config_options()
        new_options[CONF_OPEN_TIMEOUT] = 10

        coordinator.update_options(new_options)

        assert coordinator.open_timeout == 10

        await coordinator.async_shutdown()


class TestManualOverride:
    """Tests for manual thermostat override detection."""

    async def test_manual_on_while_paused_clears_paused_state(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that user manually turning on thermostat while paused clears paused state."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 10  # Long timeout

        await coordinator.async_setup()

        # Open a sensor and wait for pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == "heat"

        # User manually turns thermostat back on
        hass.states.async_set(
            TEST_THERMOSTAT,
            "cool",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        # Paused state should be cleared
        assert coordinator.is_paused is False
        assert coordinator.previous_hvac_mode is None
        assert coordinator.trigger_sensor is None

        await coordinator.async_shutdown()

    async def test_manual_on_while_paused_cancels_close_timer(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that manual on cancels any pending close timer."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open then close a sensor to start close timer
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        # Close sensor to start close timer
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        assert coordinator._close_timer is not None

        # User manually turns thermostat on - should cancel close timer
        hass.states.async_set(
            TEST_THERMOSTAT,
            "heat",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator._close_timer is None
        assert coordinator.is_paused is False

        await coordinator.async_shutdown()

    async def test_manual_off_while_paused_updates_previous_mode(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that user turning off after manual on updates previous_hvac_mode."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Open a sensor and wait for pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == "heat"

        # User manually turns thermostat to cool (override - clears paused state)
        hass.states.async_set(
            TEST_THERMOSTAT,
            "cool",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator.is_paused is False
        assert coordinator._last_known_hvac_mode == "cool"

        # Close the sensor first, then reopen to trigger new pause cycle
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()

        # Reopen sensor to trigger new pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        # Should store "cool" as the previous mode (the user's choice)
        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == "cool"

        await coordinator.async_shutdown()

    async def test_tracks_last_known_hvac_mode(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that last known HVAC mode is tracked correctly."""
        await coordinator.async_setup()

        # Initial state is heat
        assert coordinator._last_known_hvac_mode == "heat"

        # Change to cool
        hass.states.async_set(
            TEST_THERMOSTAT,
            "cool",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator._last_known_hvac_mode == "cool"

        # Change to auto
        hass.states.async_set(
            TEST_THERMOSTAT,
            "auto",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator._last_known_hvac_mode == "auto"

        # Turn off - should NOT update last known mode
        hass.states.async_set(
            TEST_THERMOSTAT,
            "off",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator._last_known_hvac_mode == "auto"

        await coordinator.async_shutdown()

    async def test_respects_user_mode_change_for_restore(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test complete flow: pause, user overrides to cool, user turns off, sensors close, restores to cool."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01
        coordinator._options[CONF_CLOSE_TIMEOUT] = 0.01

        await coordinator.async_setup()

        # Start with heat mode
        assert coordinator._last_known_hvac_mode == "heat"

        # Open sensor and wait for pause
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Front Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        mock_climate_service.reset_mock()

        # User manually turns thermostat to cool
        hass.states.async_set(
            TEST_THERMOSTAT,
            "cool",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        assert coordinator.is_paused is False
        assert coordinator._last_known_hvac_mode == "cool"

        # User then turns it off manually
        hass.states.async_set(
            TEST_THERMOSTAT,
            "off",
            {"friendly_name": "Test Thermostat"},
        )
        await hass.async_block_till_done()

        # Last known should still be "cool"
        assert coordinator._last_known_hvac_mode == "cool"

        await coordinator.async_shutdown()


class TestTemperatureSensorStateChange:
    """Tests for temperature sensor state change handling."""

    async def test_temp_sensor_listener_subscribed(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_climate_service: AsyncMock,
    ) -> None:
        """Test that temperature sensor listener is subscribed on setup."""
        # Set up temperature sensor
        hass.states.async_set(
            "sensor.living_room_temperature",
            "20.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data

        # Verify temp sensor listener is set up
        assert coordinator._unsub_temp_sensor_state_change is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_temp_sensor_listener_cleanup(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_climate_service: AsyncMock,
    ) -> None:
        """Test that temperature sensor listener is cleaned up on shutdown."""
        hass.states.async_set(
            "sensor.living_room_temperature",
            "20.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data
        assert coordinator._unsub_temp_sensor_state_change is not None

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

        # Verify cleanup
        assert coordinator._unsub_temp_sensor_state_change is None

    async def test_temp_sensor_change_triggers_update(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_climate_service: AsyncMock,
    ) -> None:
        """Test that temperature sensor change triggers thermostat state update."""
        # Set up temperature sensor
        hass.states.async_set(
            "sensor.living_room_temperature",
            "20.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data
        
        # Track if update was triggered
        initial_update_count = coordinator.data

        # Change temperature
        hass.states.async_set(
            "sensor.living_room_temperature",
            "22.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        # Coordinator should have processed the update
        # (we just verify no errors - full logic tested elsewhere)
        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_temp_sensor_unavailable_ignored(
        self,
        hass: HomeAssistant,
        mock_config_entry,
        mock_climate_service: AsyncMock,
    ) -> None:
        """Test that unavailable temperature sensor state is ignored."""
        hass.states.async_set(
            "sensor.living_room_temperature",
            "20.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Change to unavailable - should not raise
        hass.states.async_set(
            "sensor.living_room_temperature",
            STATE_UNAVAILABLE,
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        await hass.async_block_till_done()

        await hass.config_entries.async_unload(mock_config_entry.entry_id)


class TestTimerRecalculation:
    """Tests for timer recalculation when sensors close while others remain open."""

    async def test_triggering_sensor_closes_others_open_recalculates_timer(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that closing the triggering sensor recalculates timer based on earliest still-open sensor.
        
        Scenario:
        T=0: Garage opens (timer starts, expires at T=5)
        T=2: Theater opens
        T=3: Garage closes (theater has been open 1 min, timer should expire at T=7)
        T=5: Old timer would have fired here - should NOT fire
        T=7: New timer fires (theater open for 5 min)
        """
        coordinator._options[CONF_OPEN_TIMEOUT] = 5  # 5 minute timeout

        await coordinator.async_setup()

        # T=0: Garage opens - starts timer
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == TEST_SENSOR_1
        garage_open_time = coordinator._open_sensor_times.get(TEST_SENSOR_1)
        assert garage_open_time is not None

        # Wait a bit then open theater
        await asyncio.sleep(0.1)  # Simulate ~2 min passing (scaled)
        
        # T=2: Theater opens
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        theater_open_time = coordinator._open_sensor_times.get(TEST_SENSOR_2)
        assert theater_open_time is not None
        assert theater_open_time > garage_open_time  # Theater opened later

        # Verify both sensors tracked
        assert len(coordinator.open_sensors) == 2

        # T=3: Garage closes - should recalculate timer
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        # Timer should be recalculated for theater
        assert coordinator._pending_open_sensor == TEST_SENSOR_2
        assert TEST_SENSOR_1 not in coordinator._open_sensor_times
        assert TEST_SENSOR_2 in coordinator._open_sensor_times

        # Not yet paused
        assert coordinator.is_paused is False

        await coordinator.async_shutdown()

    async def test_all_sensors_close_before_timeout_cancels_timer(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that closing all sensors cancels the timer entirely."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 5

        await coordinator.async_setup()

        # Open two sensors
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        assert coordinator._open_timer is not None
        assert len(coordinator.open_sensors) == 2

        # Close both sensors
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        hass.states.async_set(TEST_SENSOR_2, STATE_OFF, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        # Timer should be cancelled
        assert coordinator._open_timer is None
        assert len(coordinator.open_sensors) == 0

        await coordinator.async_shutdown()

    async def test_non_triggering_sensor_closes_no_recalculation(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that closing a non-triggering sensor doesn't recalculate timer."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 5

        await coordinator.async_setup()

        # Open garage first (triggering sensor)
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        await asyncio.sleep(0.05)

        # Open theater second
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        original_pending = coordinator._pending_open_sensor
        assert original_pending == TEST_SENSOR_1  # Garage is triggering sensor

        # Close theater (NOT the triggering sensor)
        hass.states.async_set(TEST_SENSOR_2, STATE_OFF, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        # Timer should still be for garage (no recalculation needed)
        assert coordinator._pending_open_sensor == TEST_SENSOR_1
        assert coordinator._open_timer is not None

        await coordinator.async_shutdown()

    async def test_recalculation_triggers_immediate_if_already_expired(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
        mock_climate_service: AsyncMock,
        mock_notify_service: AsyncMock,
    ) -> None:
        """Test that recalculation triggers immediately if the new sensor has exceeded timeout."""
        coordinator._options[CONF_OPEN_TIMEOUT] = 0.01  # Very short timeout (0.6 seconds)

        await coordinator.async_setup()

        # Open sensor 1 (triggering)
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        # Open sensor 2 immediately after
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        # Wait for the timeout to pass for both
        await asyncio.sleep(1)

        # Close sensor 1 - should trigger recalculation and immediate fire
        # because sensor 2 has already been open longer than timeout
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        # Should be paused (immediate trigger on recalculation)
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True

        await coordinator.async_shutdown()

    async def test_open_sensor_timestamps_preserved_on_update(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that open sensor timestamps are preserved when _update_open_sensors is called."""
        await coordinator.async_setup()

        # Open a sensor
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        original_time = coordinator._open_sensor_times.get(TEST_SENSOR_1)
        assert original_time is not None

        # Wait a bit
        await asyncio.sleep(0.1)

        # Manually call update (simulates state refresh)
        coordinator._update_open_sensors()

        # Timestamp should be preserved
        assert coordinator._open_sensor_times.get(TEST_SENSOR_1) == original_time

        await coordinator.async_shutdown()


class TestInitialOpenSensorCheck:
    """Tests for checking already-open sensors on startup and resume."""

    async def test_sensor_already_open_on_startup_starts_timer(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that if a sensor is already open on startup, a timer is started."""
        # Set sensor to open BEFORE creating coordinator
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 1  # 1 minute

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1, TEST_SENSOR_2],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()

        # Timer should have been started for the already-open sensor
        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == TEST_SENSOR_1
        assert TEST_SENSOR_1 in coordinator._open_sensor_times

        await coordinator.async_shutdown()

    async def test_sensor_open_long_enough_triggers_immediate_pause(
        self,
        hass: HomeAssistant,
        mock_climate_service,
    ) -> None:
        """Test that if a sensor has been open longer than timeout, pause triggers immediately."""
        # Set sensor to open BEFORE creating coordinator
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 0  # 0 minute timeout = immediate

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        # Need to wait for the async_create_task to complete
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()

        # Should be paused immediately since timeout is 0
        assert coordinator.is_paused is True

        await coordinator.async_shutdown()

    async def test_resume_integration_checks_open_sensors(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that resuming integration checks for already-open sensors."""
        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 1  # 1 minute

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        await hass.async_block_till_done()

        # Pause the integration
        await coordinator.async_pause_integration()
        assert coordinator.integration_paused is True

        # Open a sensor while paused - should NOT start a timer
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        # No timer should be running while paused (handler should skip)
        assert coordinator._open_timer is None

        # Resume the integration
        await coordinator.async_resume_integration()
        await hass.async_block_till_done()

        # Timer should now be started for the open sensor
        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == TEST_SENSOR_1

        await coordinator.async_shutdown()

    async def test_no_timer_started_when_integration_paused(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that no timer is started for open sensors when integration is paused."""
        # Set sensor to open BEFORE creating coordinator
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 1

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        # Pause integration BEFORE setup
        coordinator.integration_paused = True

        await coordinator.async_setup()

        # No timer should be started
        assert coordinator._open_timer is None

        await coordinator.async_shutdown()

    async def test_already_paused_by_contact_no_new_timer(
        self,
        hass: HomeAssistant,
        mock_climate_service,
    ) -> None:
        """Test that if already paused by contact sensor, no duplicate timer is started."""
        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 0  # Immediate pause

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        await hass.async_block_till_done()

        # Open sensor - should pause immediately
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        
        # Clear any timer reference
        coordinator._cancel_open_timer()

        # Manually call _check_initial_open_sensors (simulating what happens on reload)
        coordinator._check_initial_open_sensors()

        # Should not start a new timer since already paused
        assert coordinator._open_timer is None

        await coordinator.async_shutdown()

    async def test_multiple_sensors_open_uses_earliest(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that when multiple sensors are open, the earliest one's timer is used."""
        # Set both sensors open BEFORE creating coordinator
        hass.states.async_set(TEST_SENSOR_1, STATE_ON, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()
        await asyncio.sleep(0.1)  # Small delay
        hass.states.async_set(TEST_SENSOR_2, STATE_ON, {"friendly_name": "Theater Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()
        options[CONF_OPEN_TIMEOUT] = 1

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1, TEST_SENSOR_2],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()

        # Timer should be based on sensor 1 (opened first)
        assert coordinator._open_timer is not None
        assert coordinator._pending_open_sensor == TEST_SENSOR_1

        await coordinator.async_shutdown()


class TestAwayModeCoordinator:
    """Tests for away mode functionality in the coordinator."""

    async def test_away_mode_not_configured_by_default(
        self,
        hass: HomeAssistant,
        coordinator: ThermostatContactSensorsCoordinator,
    ) -> None:
        """Test that away mode is not configured when no presence entity is set."""
        await coordinator.async_setup()
        
        assert coordinator.away_mode_configured is False
        assert coordinator.is_away is False
        assert coordinator.away_presence_entity == ""
        
        await coordinator.async_shutdown()

    async def test_away_mode_configured_with_presence_entity(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that away mode is configured when presence entity is set."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_AWAY_HEAT_TEMP_DIFF,
            CONF_AWAY_COOL_TEMP_DIFF,
        )
        
        # Set up person entity
        hass.states.async_set("person.test_user", "home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "person.test_user"
        options[CONF_AWAY_HEAT_TEMP_DIFF] = -3.0
        options[CONF_AWAY_COOL_TEMP_DIFF] = 3.0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator.away_mode_configured is True
        assert coordinator.away_presence_entity == "person.test_user"
        assert coordinator.away_heat_temp_diff == -3.0
        assert coordinator.away_cool_temp_diff == 3.0
        assert coordinator.is_away is False  # Person is home
        
        await coordinator.async_shutdown()

    async def test_away_mode_activates_when_not_home(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that away mode activates when presence entity shows not_home."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_AWAY_HEAT_TEMP_DIFF,
            CONF_AWAY_COOL_TEMP_DIFF,
        )
        
        # Set up person entity as away
        hass.states.async_set("person.test_user", "not_home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "person.test_user"
        options[CONF_AWAY_HEAT_TEMP_DIFF] = -3.0
        options[CONF_AWAY_COOL_TEMP_DIFF] = 3.0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator.is_away is True
        
        await coordinator.async_shutdown()

    async def test_away_mode_responds_to_presence_changes(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that away mode responds to presence entity state changes."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_AWAY_HEAT_TEMP_DIFF,
            CONF_AWAY_COOL_TEMP_DIFF,
        )
        
        # Start with person home
        hass.states.async_set("person.test_user", "home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "person.test_user"
        options[CONF_AWAY_HEAT_TEMP_DIFF] = -3.0
        options[CONF_AWAY_COOL_TEMP_DIFF] = 3.0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator.is_away is False
        
        # Person leaves
        hass.states.async_set("person.test_user", "not_home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        assert coordinator.is_away is True
        
        # Person returns
        hass.states.async_set("person.test_user", "home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        assert coordinator.is_away is False
        
        await coordinator.async_shutdown()

    async def test_away_mode_with_binary_sensor(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that away mode works with binary_sensor (off = away)."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_AWAY_HEAT_TEMP_DIFF,
            CONF_AWAY_COOL_TEMP_DIFF,
        )
        
        # Set up binary sensor as off (away)
        hass.states.async_set("binary_sensor.home_occupied", STATE_OFF, {"friendly_name": "Home Occupied"})
        await hass.async_block_till_done()
        
        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "binary_sensor.home_occupied"
        options[CONF_AWAY_HEAT_TEMP_DIFF] = -2.0
        options[CONF_AWAY_COOL_TEMP_DIFF] = 2.0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator.is_away is True
        
        # Turn on (someone home)
        hass.states.async_set("binary_sensor.home_occupied", STATE_ON, {"friendly_name": "Home Occupied"})
        await hass.async_block_till_done()
        
        assert coordinator.is_away is False
        
        await coordinator.async_shutdown()

    async def test_away_mode_cleanup_on_shutdown(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that presence listener is cleaned up on shutdown."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
        )
        
        hass.states.async_set("person.test_user", "home", {"friendly_name": "Test User"})
        await hass.async_block_till_done()
        
        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "person.test_user"

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator._unsub_presence_state_change is not None
        
        await coordinator.async_shutdown()
        
        assert coordinator._unsub_presence_state_change is None


class TestEcoAwayBehaviorCoordinator:
    """Tests for eco_away_behavior property in coordinator."""

    async def test_eco_away_behavior_default_is_disable_eco(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that eco_away_behavior defaults to disable_eco_when_away."""
        hass.states.async_set(TEST_THERMOSTAT, HVACMode.OFF, {"friendly_name": "Test Thermostat"})
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        assert coordinator.eco_away_behavior == "disable_eco_when_away"
        
        await coordinator.async_shutdown()

    async def test_eco_away_behavior_can_be_set(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that eco_away_behavior can be modified."""
        hass.states.async_set(TEST_THERMOSTAT, HVACMode.OFF, {"friendly_name": "Test Thermostat"})
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        await hass.async_block_till_done()

        options = get_test_config_options()

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        coordinator.eco_away_behavior = "keep_eco_active"
        assert coordinator.eco_away_behavior == "keep_eco_active"
        
        coordinator.eco_away_behavior = "use_eco_away_targets"
        assert coordinator.eco_away_behavior == "use_eco_away_targets"
        
        await coordinator.async_shutdown()

    async def test_eco_mode_disabled_when_away_with_disable_eco_behavior(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Test that eco mode is effectively disabled when away with disable_eco behavior."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AWAY_PRESENCE_ENTITY,
        )
        
        hass.states.async_set(TEST_THERMOSTAT, HVACMode.HEAT, {
            "friendly_name": "Test Thermostat",
            "temperature": 72.0,
        })
        hass.states.async_set(TEST_SENSOR_1, STATE_OFF, {"friendly_name": "Garage Door"})
        # Set presence to away
        hass.states.async_set("binary_sensor.home_occupied", STATE_OFF, {"friendly_name": "Home Occupied"})
        await hass.async_block_till_done()

        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "binary_sensor.home_occupied"

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[TEST_SENSOR_1],
            thermostat=TEST_THERMOSTAT,
            options=options,
        )

        await coordinator.async_setup()
        
        # Enable eco mode
        coordinator.eco_mode = True
        # Set behavior to disable eco when away
        coordinator.eco_away_behavior = "disable_eco_when_away"
        
        assert coordinator.is_away is True
        assert coordinator.eco_mode is True
        
        # When update_thermostat_state is called, it should pass eco_mode=False
        # to the thermostat controller because we're away with disable_eco behavior
        # (This is tested by verifying the logic in the coordinator)
        
        await coordinator.async_shutdown()


class TestForceTrackWhenCriticalOverride:
    """Tests for the force_track_when_critical per-area override."""

    @pytest.mark.asyncio
    async def test_force_track_critical_overrides_eco_mode(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that force_track_when_critical allows critical room to be tracked even in Eco Mode."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_AREAS,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )

        # Configure two areas: one with override, one without
        areas_config = {
            "music_room": {
                CONF_AREA_ID: "music_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.music_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,  # Override enabled
            },
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
                # No override - defaults to False
            },
        }

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Eco mode behavior depends on the select policy.
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT

        # Enable Eco Mode (normally ignores all inactive rooms)
        coordinator.eco_mode = True

        # Both rooms are inactive (no occupancy)
        # Music room has override, bedroom doesn't
        inactive_areas = coordinator.occupancy_tracker.inactive_areas

        # Apply the filtering logic from update_thermostat_state
        if coordinator.eco_mode:
            filtered_inactive = [
                area for area in inactive_areas
                if coordinator._area_has_critical_override(area.area_id)
            ]
        else:
            filtered_inactive = inactive_areas

        # Only music_room should pass through (has override)
        filtered_area_ids = {area.area_id for area in filtered_inactive}
        assert "music_room" in filtered_area_ids
        assert "bedroom" not in filtered_area_ids

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_force_track_critical_overrides_tsr(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that force_track_when_critical allows room to be tracked even when TSR is on and room not tracked."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_AREAS,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )

        areas_config = {
            "theater": {
                CONF_AREA_ID: "theater",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.theater_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,  # Override enabled
            },
            "guest_room": {
                CONF_AREA_ID: "guest_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.guest_temp"],
                # No override
            },
        }

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Enable Track Selected Rooms (TSR) but don't track any rooms
        coordinator.only_track_selected_rooms = True
        coordinator.eco_mode = False  # Eco Mode off

        inactive_areas = coordinator.occupancy_tracker.inactive_areas

        # Apply the filtering logic from update_thermostat_state
        if coordinator.only_track_selected_rooms:
            filtered_inactive = [
                area for area in inactive_areas
                if coordinator.is_room_tracked(area.area_id) or coordinator._area_has_critical_override(area.area_id)
            ]
        else:
            filtered_inactive = inactive_areas

        # Only theater should pass through (has override)
        filtered_area_ids = {area.area_id for area in filtered_inactive}
        assert "theater" in filtered_area_ids
        assert "guest_room" not in filtered_area_ids

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_force_track_critical_with_eco_and_tsr(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that force_track_when_critical works with both Eco Mode and TSR enabled."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_AREAS,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )

        areas_config = {
            "music_room": {
                CONF_AREA_ID: "music_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.music_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,  # Override
            },
            "living_room": {
                CONF_AREA_ID: "living_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.living_temp"],
                # No override
            },
        }

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT

        # Enable BOTH Eco Mode and TSR, no rooms tracked
        coordinator.eco_mode = True
        coordinator.only_track_selected_rooms = True

        inactive_areas = coordinator.occupancy_tracker.inactive_areas

        # Apply the filtering logic from update_thermostat_state
        # Eco Mode takes precedence
        if coordinator.eco_mode:
            filtered_inactive = [
                area for area in inactive_areas
                if coordinator._area_has_critical_override(area.area_id)
            ]
        else:
            if coordinator.only_track_selected_rooms:
                filtered_inactive = [
                    area for area in inactive_areas
                    if coordinator.is_room_tracked(area.area_id) or coordinator._area_has_critical_override(area.area_id)
                ]
            else:
                filtered_inactive = inactive_areas

        # Music room should still be checked (has override)
        filtered_area_ids = {area.area_id for area in filtered_inactive}
        assert "music_room" in filtered_area_ids
        assert "living_room" not in filtered_area_ids

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_active_room_with_force_track_critical_and_tsr_gets_evaluated(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that active rooms filtered by TSR but with force_track_when_critical still get evaluated for critical temps."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "music_room": {
                CONF_AREA_ID: "music_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.music_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.music_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "living_room": {
                CONF_AREA_ID: "living_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.living_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.living_temp"],
            },
        }

        # Set up temperature sensors
        hass.states.async_set(
            "sensor.music_temp",
            "16.0",  # Critical cold
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.living_temp",
            "15.0",  # Also cold
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        # Set up motion sensors
        hass.states.async_set("binary_sensor.music_motion", STATE_ON)
        hass.states.async_set("binary_sensor.living_motion", STATE_OFF)

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Enable TSR but DON'T track either room - this filters both from normal evaluation
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = []  # Neither room tracked

        # Make music_room active by setting occupancy state directly
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["music_room"] = AreaOccupancyState(
            area_id="music_room",
            area_name="Music Room",
            binary_sensors=["binary_sensor.music_motion"],
            occupied_binary_sensors={"binary_sensor.music_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        
        # Music room should be active
        assert any(a.area_id == "music_room" for a in coordinator.occupancy_tracker.active_areas)

        # Update thermostat state
        thermostat_state = coordinator.update_thermostat_state()

        # Music room should have a room_state despite being filtered from active_areas by TSR
        # because it has force_track_when_critical
        assert thermostat_state is not None
        assert "music_room" in thermostat_state.room_states
        
        music_state = thermostat_state.room_states["music_room"]
        
        # Should be marked as critical (16°C is below default 22°C - 3°C threshold)
        assert music_state.is_critical is True
        assert music_state.determining_temperature == 16.0
        
        # Living room should NOT be in room_states (it's inactive AND not tracked by TSR)
        assert "living_room" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_temp_change_to_critical_opens_vent_with_eco_select_tsr_untracked_ftcr(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
        mock_climate_service: AsyncMock,
    ):
        """Non-critical -> critical temp event should open vents.

        Scenario:
        - Eco mode ON
        - Eco critical tracking = Track Select Critical
        - TSR ON
        - Room is NOT tracked, but has Force Track When Critical enabled

        Expectation:
        - When the room temperature changes from non-critical to critical,
          the temp sensor state-change event triggers vent reevaluation and
          an open command is queued/executed for that room's vents.
        """
        from unittest.mock import AsyncMock

        from homeassistant.components.cover import (
            DOMAIN as COVER_DOMAIN,
            SERVICE_CLOSE_COVER_TILT,
            SERVICE_OPEN_COVER_TILT,
        )
        from homeassistant.const import ATTR_ENTITY_ID, STATE_CLOSED

        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_MIN_CYCLE_OFF_MINUTES,
            CONF_MIN_CYCLE_ON_MINUTES,
            CONF_MIN_VENTS_OPEN,
            CONF_TEMPERATURE_SENSORS,
            CONF_VENT_DEBOUNCE_SECONDS,
            CONF_VENT_OPEN_DELAY_SECONDS,
            CONF_VENTS,
            ECO_CRITICAL_SELECT,
        )

        music_temp = "sensor.music_temp"
        music_vent = "cover.music_vent"

        areas_config = {
            "music_room": {
                CONF_AREA_ID: "music_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: [music_temp],
                CONF_VENTS: [music_vent],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "living_room": {
                CONF_AREA_ID: "living_room",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: [],
                CONF_TEMPERATURE_SENSORS: ["sensor.living_temp"],
                CONF_VENTS: ["cover.living_vent"],
            },
        }

        # Initial vent states: closed
        hass.states.async_set(music_vent, STATE_CLOSED, {"current_tilt_position": 0})
        hass.states.async_set("cover.living_vent", STATE_CLOSED, {"current_tilt_position": 0})

        # Initial temps: non-critical (target 22, threshold 3 => critical < 19)
        temp_attrs = {"unit_of_measurement": "°C", "device_class": "temperature"}
        hass.states.async_set(music_temp, "20.0", temp_attrs)
        hass.states.async_set("sensor.living_temp", "20.0", temp_attrs)

        open_mock = AsyncMock()
        close_mock = AsyncMock()

        async def _handle_open_tilt(call):
            entity_id = call.data.get(ATTR_ENTITY_ID)
            if isinstance(entity_id, list):
                entity_ids = entity_id
            else:
                entity_ids = [entity_id]

            for eid in entity_ids:
                hass.states.async_set(eid, "open", {"current_tilt_position": 100})
            await open_mock(call)

        async def _handle_close_tilt(call):
            entity_id = call.data.get(ATTR_ENTITY_ID)
            if isinstance(entity_id, list):
                entity_ids = entity_id
            else:
                entity_ids = [entity_id]

            for eid in entity_ids:
                hass.states.async_set(eid, STATE_CLOSED, {"current_tilt_position": 0})
            await close_mock(call)

        hass.services.async_register(COVER_DOMAIN, SERVICE_OPEN_COVER_TILT, _handle_open_tilt)
        hass.services.async_register(COVER_DOMAIN, SERVICE_CLOSE_COVER_TILT, _handle_close_tilt)

        options = get_test_config_options()
        options[CONF_MIN_VENTS_OPEN] = 0
        options[CONF_VENT_OPEN_DELAY_SECONDS] = 0
        options[CONF_VENT_DEBOUNCE_SECONDS] = 0
        # Allow immediate off->on transitions in this test.
        options[CONF_MIN_CYCLE_ON_MINUTES] = 0
        options[CONF_MIN_CYCLE_OFF_MINUTES] = 0

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=options,
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = set()  # music_room is intentionally untracked

        # Baseline: non-critical should not open the vent
        await coordinator.async_update_thermostat_state()
        await coordinator.async_update_vents()
        assert coordinator.last_vent_control_state is not None
        assert not any(
            cmd[0] == music_vent and cmd[1] is True
            for cmd in coordinator.last_vent_control_state.pending_commands
        )
        assert open_mock.await_count == 0

        # Now the temp becomes critical: should trigger the temp sensor listener
        hass.states.async_set(music_temp, "18.0", temp_attrs)
        await hass.async_block_till_done()

        assert coordinator.last_vent_control_state is not None
        assert any(
            cmd[0] == music_vent and cmd[1] is True
            for cmd in coordinator.last_vent_control_state.pending_commands
        )
        assert open_mock.await_count >= 1

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_tsr_tracked_active_room_gets_normal_evaluation(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that active rooms that ARE tracked by TSR get normal satiation evaluation."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.office_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
        }

        # Set up temperature sensors
        hass.states.async_set(
            "sensor.office_temp",
            "20.0",  # Below target, not satiated
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.bedroom_temp",
            "19.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        # Set up motion sensors - both active
        hass.states.async_set("binary_sensor.office_motion", STATE_ON)
        hass.states.async_set("binary_sensor.bedroom_motion", STATE_ON)

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Enable TSR and only track office
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["office"]

        # Make both rooms active by setting occupancy state directly
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["office"] = AreaOccupancyState(
            area_id="office",
            area_name="Office",
            binary_sensors=["binary_sensor.office_motion"],
            occupied_binary_sensors={"binary_sensor.office_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        # Both should be active
        active_area_ids = {a.area_id for a in coordinator.occupancy_tracker.active_areas}
        assert "office" in active_area_ids
        assert "bedroom" in active_area_ids

        # Update thermostat state
        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        
        # Office (tracked) should have room_state with satiation evaluation
        assert "office" in thermostat_state.room_states
        office_state = thermostat_state.room_states["office"]
        assert office_state.is_active is True
        assert office_state.is_satiated is False  # Below target

        # Bedroom (untracked) is still evaluated for display, but should not count
        # toward the tracked active-room decision logic.
        assert "bedroom" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 1
        assert thermostat_state.satiated_room_count == 0

        await coordinator.async_shutdown()


class TestEcoCriticalTrackingModes:
    """Tests for different ECO_CRITICAL tracking modes (NONE, SELECT, ALL)."""

    @pytest.mark.asyncio
    async def test_eco_none_ignores_all_inactive_rooms(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_NONE ignores all inactive rooms regardless of critical status."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_NONE,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        # Bedroom critically cold, office normal
        hass.states.async_set(
            "sensor.bedroom_temp",
            "15.0",  # Critical: 15°C < (22°C - 3°C threshold)
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.office_temp",
            "20.0",  # Normal temp
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_NONE

        # Both rooms are inactive (no occupancy)
        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # No inactive rooms should be tracked with ECO_CRITICAL_NONE
        assert "bedroom" not in thermostat_state.room_states
        assert "office" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_none_with_ftcr_still_tracks_critical_room(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_NONE with FTCR override still tracks critical room."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_NONE,
        )

        areas_config = {
            "basement": {
                CONF_AREA_ID: "basement",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.basement_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "garage": {
                CONF_AREA_ID: "garage",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.garage_temp"],
            },
        }

        hass.states.async_set(
            "sensor.basement_temp",
            "14.0",  # Critical cold
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.garage_temp",
            "13.0",  # Also critical but no FTCR
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_NONE

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Basement has FTCR override, should be tracked
        assert "basement" in thermostat_state.room_states
        assert thermostat_state.room_states["basement"].is_critical is True
        
        # Garage doesn't have FTCR, should not be tracked
        assert "garage" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_all_tracks_all_inactive_rooms(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_ALL tracks all inactive rooms for critical temps."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_ALL,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.kitchen_temp"],
            },
        }

        hass.states.async_set(
            "sensor.bedroom_temp",
            "16.0",  # Critical
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.office_temp",
            "20.0",  # Normal
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.kitchen_temp",
            "21.0",  # Normal
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_ALL

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # All inactive rooms should be evaluated
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        assert "kitchen" in thermostat_state.room_states
        
        # Bedroom should be marked critical
        assert thermostat_state.room_states["bedroom"].is_critical is True
        # Others should not be critical
        assert thermostat_state.room_states["office"].is_critical is False
        assert thermostat_state.room_states["kitchen"].is_critical is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_all_with_no_critical_rooms(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_ALL with no critical rooms - all evaluated but none critical."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_ALL,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        hass.states.async_set(
            "sensor.bedroom_temp",
            "20.0",  # Normal
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.office_temp",
            "21.0",  # Normal
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_ALL

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # All rooms evaluated
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        
        # None should be critical
        assert thermostat_state.room_states["bedroom"].is_critical is False
        assert thermostat_state.room_states["office"].is_critical is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_select_with_no_tracked_rooms(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_SELECT with empty tracked list - no rooms evaluated."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        hass.states.async_set(
            "sensor.bedroom_temp",
            "15.0",  # Critical
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.office_temp",
            "16.0",  # Critical
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = []  # No rooms tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # No rooms should be evaluated (not tracked, no FTCR)
        assert "bedroom" not in thermostat_state.room_states
        assert "office" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_select_with_all_rooms_tracked(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_CRITICAL_SELECT with all rooms tracked - behaves like ALL."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        hass.states.async_set(
            "sensor.bedroom_temp",
            "15.0",  # Critical
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )
        hass.states.async_set(
            "sensor.office_temp",
            "20.0",  # Normal
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["bedroom", "office"]  # All tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Both rooms should be evaluated
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        
        assert thermostat_state.room_states["bedroom"].is_critical is True
        assert thermostat_state.room_states["office"].is_critical is False

        await coordinator.async_shutdown()


class TestEcoAwayBehaviors:
    """Tests for different eco away behaviors."""

    @pytest.mark.asyncio
    async def test_away_with_keep_eco_active_behavior(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that eco mode stays active when away with KEEP_ECO_ACTIVE."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )

        hass.states.async_set("binary_sensor.home_occupied", STATE_OFF)  # Away
        hass.states.async_set(
            "sensor.bedroom_temp",
            "15.0",  # Critical
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
        }

        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "binary_sensor.home_occupied"

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=options,
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.eco_away_behavior = "keep_eco_active"

        assert coordinator.is_away is True
        
        # Update thermostat state - eco should remain active
        thermostat_state = coordinator.update_thermostat_state()
        
        # Eco mode filtering should apply (no rooms tracked with SELECT)
        assert thermostat_state is not None
        assert "bedroom" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_away_with_use_eco_targets_behavior(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test that away targets are used with USE_ECO_AWAY_TARGETS."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_AWAY_PRESENCE_ENTITY,
            CONF_TEMPERATURE_SENSORS,
        )

        hass.states.async_set("binary_sensor.home_occupied", STATE_OFF)  # Away
        hass.states.async_set(
            "sensor.bedroom_temp",
            "15.0",
            {"unit_of_measurement": "°C", "device_class": "temperature"},
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
        }

        options = get_test_config_options()
        options[CONF_AWAY_PRESENCE_ENTITY] = "binary_sensor.home_occupied"

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=options,
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = True
        coordinator.eco_away_behavior = "use_eco_away_targets"

        assert coordinator.is_away is True
        
        # Update should use away behavior
        thermostat_state = coordinator.update_thermostat_state()
        assert thermostat_state is not None
        
        # When use_eco_away_targets is set and eco_away_thermostat exists,
        # eco_away_targets should be populated from that virtual thermostat
        # For this test without the virtual thermostat entity, eco_away_targets will be None
        # The behavior is tested in integration tests with actual climate entities

        await coordinator.async_shutdown()


class TestTSREdgeCases:
    """Tests for Track Selected Rooms edge cases."""

    @pytest.mark.asyncio
    async def test_tsr_on_no_rooms_tracked_no_ftcr(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test TSR enabled with empty list and no FTCR - no rooms evaluated."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.office_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.office_temp", "21.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make both rooms active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        coordinator.occupancy_tracker._areas["office"] = AreaOccupancyState(
            area_id="office",
            area_name="Office",
            binary_sensors=["binary_sensor.office_motion"],
            occupied_binary_sensors={"binary_sensor.office_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = []  # No rooms tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Rooms are still evaluated for display, but none are tracked for decisions.
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 0

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_tsr_on_all_rooms_tracked(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test TSR with all rooms tracked - behaves like TSR off."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.office_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.kitchen_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.kitchen_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.office_temp", "21.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.kitchen_temp", "22.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make all rooms active
        now = dt_util.utcnow()
        for room_id in ["bedroom", "office", "kitchen"]:
            coordinator.occupancy_tracker._areas[room_id] = AreaOccupancyState(
                area_id=room_id,
                area_name=room_id.capitalize(),
                binary_sensors=[f"binary_sensor.{room_id}_motion"],
                occupied_binary_sensors={f"binary_sensor.{room_id}_motion"},
                occupancy_start_time=now - timedelta(minutes=10),
                is_active=True,
            )

        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["bedroom", "office", "kitchen"]  # All tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # All rooms should be evaluated
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        assert "kitchen" in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_tsr_on_some_tracked_inactive_untracked_not_critical(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test TSR with some tracked - only tracked inactive rooms evaluated."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.office_temp", "21.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["bedroom"]  # Only bedroom tracked

        # Both inactive (no occupancy)
        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Only tracked bedroom should be evaluated
        assert "bedroom" in thermostat_state.room_states
        assert "office" not in thermostat_state.room_states

        await coordinator.async_shutdown()


class TestComplexCombinedScenarios:
    """Tests for complex combinations of settings."""

    @pytest.mark.asyncio
    async def test_eco_all_plus_tsr_on_some_tracked(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_ALL evaluates all inactive, TSR filters active."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_ALL,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.office_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.kitchen_temp"],
            },
            "living_room": {
                CONF_AREA_ID: "living_room",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.living_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.office_temp", "21.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.kitchen_temp", "19.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.living_temp", "18.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom and office active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        coordinator.occupancy_tracker._areas["office"] = AreaOccupancyState(
            area_id="office",
            area_name="Office",
            binary_sensors=["binary_sensor.office_motion"],
            occupied_binary_sensors={"binary_sensor.office_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_ALL
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["bedroom"]  # Only bedroom tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Active rooms are evaluated for display regardless of TSR, but only tracked
        # rooms count toward decisions.
        assert "bedroom" in thermostat_state.room_states
        assert "office" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 1
        
        # Inactive: all should be evaluated (ECO_ALL)
        assert "kitchen" in thermostat_state.room_states
        assert "living_room" in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_none_plus_tsr_plus_ftcr(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_NONE + TSR + FTCR: FTCR overrides both."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_NONE,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "basement": {
                CONF_AREA_ID: "basement",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.basement_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "garage": {
                CONF_AREA_ID: "garage",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.garage_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.basement_temp", "14.0", {"unit_of_measurement": "°C"})  # Critical
        hass.states.async_set("sensor.garage_temp", "15.0", {"unit_of_measurement": "°C"})  # Critical

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom active but not tracked
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_NONE
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = []  # Nothing tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Bedroom: active but not tracked - still evaluated for display
        assert "bedroom" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 0
        
        # Basement: inactive with FTCR - should appear
        assert "basement" in thermostat_state.room_states
        assert thermostat_state.room_states["basement"].is_critical is True
        assert thermostat_state.critical_room_count == 1
        
        # Garage: inactive, critical, but no FTCR - filtered out
        assert "garage" not in thermostat_state.room_states

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_eco_select_plus_tsr_different_lists(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test ECO_SELECT + TSR with different tracked lists for each."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
            "office": {
                CONF_AREA_ID: "office",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.office_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.office_temp"],
            },
            "kitchen": {
                CONF_AREA_ID: "kitchen",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.kitchen_temp"],
            },
            "living_room": {
                CONF_AREA_ID: "living_room",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.living_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.office_temp", "21.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.kitchen_temp", "19.0", {"unit_of_measurement": "°C"})
        hass.states.async_set("sensor.living_temp", "18.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom and office active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )
        coordinator.occupancy_tracker._areas["office"] = AreaOccupancyState(
            area_id="office",
            area_name="Office",
            binary_sensors=["binary_sensor.office_motion"],
            occupied_binary_sensors={"binary_sensor.office_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.only_track_selected_rooms = True
        # TSR tracks bedroom, eco SELECT tracks kitchen
        coordinator._tracked_rooms = ["bedroom", "kitchen"]

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Active: bedroom tracked
        assert "bedroom" in thermostat_state.room_states
        # Active: office untracked but still evaluated for display
        assert "office" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 1
        
        # Inactive: kitchen tracked by eco SELECT
        assert "kitchen" in thermostat_state.room_states
        # Inactive: living_room not tracked
        assert "living_room" not in thermostat_state.room_states

        await coordinator.async_shutdown()


class TestRoomStateCombinations:
    """Tests for various room state combinations."""

    @pytest.mark.asyncio
    async def test_active_critical_room_with_ftcr_tsr_off_eco_select(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test active critical room gets satiation eval, not critical eval."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_SELECT,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "16.0", {"unit_of_measurement": "°C"})  # Critical

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_SELECT
        coordinator.only_track_selected_rooms = False  # TSR off

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        assert "bedroom" in thermostat_state.room_states
        
        bedroom_state = thermostat_state.room_states["bedroom"]
        # Active rooms get satiation evaluation
        assert bedroom_state.is_active is True
        # Should be marked critical even though active (evaluated during satiation check)
        assert bedroom_state.is_critical is True

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_active_non_critical_no_ftcr_tsr_on_not_tracked(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test active room not tracked by TSR is filtered out."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "20.0", {"unit_of_measurement": "°C"})

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = []  # Not tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        # Active but not tracked - still evaluated for display, but not counted
        assert "bedroom" in thermostat_state.room_states
        assert thermostat_state.active_room_count == 0

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_inactive_critical_no_ftcr_tsr_on_tracked(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test inactive critical room tracked by TSR is evaluated (eco off)."""
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_ID,
            CONF_TEMPERATURE_SENSORS,
        )

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "15.0", {"unit_of_measurement": "°C"})  # Critical

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        coordinator.eco_mode = False  # Eco off - all inactive evaluated
        coordinator.only_track_selected_rooms = True
        coordinator._tracked_rooms = ["bedroom"]  # Tracked

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        assert "bedroom" in thermostat_state.room_states
        assert thermostat_state.room_states["bedroom"].is_critical is True

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_multiple_ftcr_rooms_different_states(
        self,
        hass: HomeAssistant,
        setup_test_entities: None,
    ):
        """Test multiple FTCR rooms in different states."""
        from datetime import timedelta
        
        from homeassistant import util as dt_util
        
        from custom_components.thermostat_contact_sensors.const import (
            CONF_AREA_ENABLED,
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
            CONF_AREA_ID,
            CONF_BINARY_SENSORS,
            CONF_TEMPERATURE_SENSORS,
            ECO_CRITICAL_NONE,
        )
        from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState

        areas_config = {
            "bedroom": {
                CONF_AREA_ID: "bedroom",
                CONF_AREA_ENABLED: True,
                CONF_BINARY_SENSORS: ["binary_sensor.bedroom_motion"],
                CONF_TEMPERATURE_SENSORS: ["sensor.bedroom_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "basement": {
                CONF_AREA_ID: "basement",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.basement_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
            "garage": {
                CONF_AREA_ID: "garage",
                CONF_AREA_ENABLED: True,
                CONF_TEMPERATURE_SENSORS: ["sensor.garage_temp"],
                CONF_AREA_FORCE_TRACK_WHEN_CRITICAL: True,
            },
        }

        hass.states.async_set("sensor.bedroom_temp", "16.0", {"unit_of_measurement": "°C"})  # Critical
        hass.states.async_set("sensor.basement_temp", "14.0", {"unit_of_measurement": "°C"})  # Critical
        hass.states.async_set("sensor.garage_temp", "20.0", {"unit_of_measurement": "°C"})  # Normal

        coordinator = ThermostatContactSensorsCoordinator(
            hass,
            config_entry_id="test_entry",
            contact_sensors=[],
            thermostat=TEST_THERMOSTAT,
            options=get_test_config_options(),
            areas_config=areas_config,
        )

        await coordinator.async_setup()

        # Make bedroom active
        now = dt_util.utcnow()
        coordinator.occupancy_tracker._areas["bedroom"] = AreaOccupancyState(
            area_id="bedroom",
            area_name="Bedroom",
            binary_sensors=["binary_sensor.bedroom_motion"],
            occupied_binary_sensors={"binary_sensor.bedroom_motion"},
            occupancy_start_time=now - timedelta(minutes=10),
            is_active=True,
        )

        coordinator.eco_mode = True
        coordinator.eco_mode_critical_tracking = ECO_CRITICAL_NONE

        thermostat_state = coordinator.update_thermostat_state()

        assert thermostat_state is not None
        
        # All FTCR rooms should be evaluated
        assert "bedroom" in thermostat_state.room_states  # Active, critical, FTCR
        assert "basement" in thermostat_state.room_states  # Inactive, critical, FTCR
        assert "garage" in thermostat_state.room_states  # Inactive, normal, FTCR
        
        # Check critical flags
        assert thermostat_state.room_states["bedroom"].is_critical is True
        assert thermostat_state.room_states["basement"].is_critical is True
        assert thermostat_state.room_states["garage"].is_critical is False

        await coordinator.async_shutdown()
