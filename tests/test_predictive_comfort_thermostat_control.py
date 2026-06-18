"""Tests for Predictive Comfort thermostat control coordination."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant

from custom_components.thermostat_contact_sensors.occupancy import AreaOccupancyState
from custom_components.thermostat_contact_sensors.thermostat_control import (
    ThermostatAction,
    ThermostatController,
)

from .conftest import TEST_THERMOSTAT


class TestPredictiveComfortThermostatControl:
    """Tests for coordinated Predictive Comfort and room thermostat demand."""

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

    def _mock_state(
        self,
        state: str,
        attrs: dict | None = None,
    ) -> MagicMock:
        """Return a mocked Home Assistant State."""
        mock_state = MagicMock()
        mock_state.state = state
        mock_state.attributes = attrs or {}
        return mock_state

    def _configure_temperature_scenario(
        self,
        controller,
        mock_hass,
        *,
        thermostat_mode: HVACMode,
        room_temperatures: dict[str, float],
        target_low: float = 71.0,
        target_high: float = 74.0,
    ) -> dict[str, AreaOccupancyState]:
        """Configure thermostat and room temperature states for control tests."""
        global_thermostat = MagicMock()
        global_thermostat.effective_target_temp_low = target_low
        global_thermostat.effective_target_temp_high = target_high
        controller._global_thermostat_getter = lambda: global_thermostat

        areas = {
            area_id: AreaOccupancyState(area_id=area_id, area_name=area_id)
            for area_id in room_temperatures
        }

        def get_state(entity_id):
            if entity_id == TEST_THERMOSTAT:
                return self._mock_state(
                    thermostat_mode.value,
                    {
                        "temperature": target_high
                        if thermostat_mode == HVACMode.COOL
                        else target_low,
                        "hvac_modes": [
                            HVACMode.OFF.value,
                            HVACMode.HEAT.value,
                            HVACMode.COOL.value,
                        ],
                    },
                )

            for area_id, temperature in room_temperatures.items():
                if entity_id == f"sensor.{area_id}_temperature":
                    return self._mock_state(
                        str(temperature),
                        {"unit_of_measurement": UnitOfTemperature.FAHRENHEIT},
                    )

            return None

        mock_hass.states.get.side_effect = get_state
        return areas

    def test_predictive_cooling_turns_on_when_main_logic_is_idle(
        self,
        controller,
        mock_hass,
    ):
        """Predictive Comfort can turn HVAC on even when rooms are not critical."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.OFF,
            room_temperatures={"living_room": 72.0},
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors={"living_room": ["sensor.living_room_temperature"]},
            inactive_areas=[areas["living_room"]],
            all_areas_for_trend=[areas["living_room"]],
            respect_user_off=False,
            predictive_hvac_mode=HVACMode.COOL,
            predictive_target_temperature=72.0,
            predictive_reason="Forecast heat will exceed the comfort band",
        )

        assert state.recommended_action == ThermostatAction.TURN_ON
        assert state.inferred_hvac_mode == HVACMode.COOL
        assert state.target_temperature == 72.0
        assert "Predictive Comfort recommends pre-cooling" in state.action_reason

    def test_main_logic_turns_on_when_predictive_is_idle(
        self,
        controller,
        mock_hass,
    ):
        """Normal room demand still turns HVAC on without predictive demand."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.OFF,
            room_temperatures={"office": 77.0},
        )
        areas["office"].is_active = True

        state = controller.evaluate_thermostat_action(
            active_areas=[areas["office"]],
            area_temp_sensors={"office": ["sensor.office_temperature"]},
            inactive_areas=[],
            all_areas_for_trend=[areas["office"]],
            respect_user_off=False,
        )

        assert state.recommended_action == ThermostatAction.TURN_ON
        assert state.inferred_hvac_mode == HVACMode.COOL
        assert state.rooms_need_cool is True

    def test_main_and_predictive_cooling_share_on_decision_and_predictive_target(
        self,
        controller,
        mock_hass,
    ):
        """When both systems want cooling, one coordinated action uses the PC target."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.OFF,
            room_temperatures={"office": 77.0},
        )
        areas["office"].is_active = True

        state = controller.evaluate_thermostat_action(
            active_areas=[areas["office"]],
            area_temp_sensors={"office": ["sensor.office_temperature"]},
            inactive_areas=[],
            all_areas_for_trend=[areas["office"]],
            respect_user_off=False,
            predictive_hvac_mode=HVACMode.COOL,
            predictive_target_temperature=72.0,
            predictive_reason="Pre-cooling before outdoor heat arrives",
        )

        assert state.recommended_action == ThermostatAction.TURN_ON
        assert state.inferred_hvac_mode == HVACMode.COOL
        assert state.target_temperature == 72.0
        assert state.rooms_need_cool is True

    def test_main_and_predictive_idle_stays_off(
        self,
        controller,
        mock_hass,
    ):
        """When neither main nor Predictive Comfort wants HVAC, it remains off."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.OFF,
            room_temperatures={"living_room": 72.0},
        )

        state = controller.evaluate_thermostat_action(
            active_areas=[],
            area_temp_sensors={"living_room": ["sensor.living_room_temperature"]},
            inactive_areas=[areas["living_room"]],
            all_areas_for_trend=[areas["living_room"]],
            respect_user_off=False,
        )

        assert state.recommended_action == ThermostatAction.NONE
        assert state.action_reason == "Already off, all rooms satiated"

    def test_opposite_predictive_mode_does_not_override_main_target(
        self,
        controller,
        mock_hass,
    ):
        """Main room demand keeps its setpoint when predictive asks for the opposite mode."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.HEAT,
            room_temperatures={"office": 68.0},
            target_low=70.0,
            target_high=74.0,
        )
        areas["office"].is_active = True

        state = controller.evaluate_thermostat_action(
            active_areas=[areas["office"]],
            area_temp_sensors={"office": ["sensor.office_temperature"]},
            inactive_areas=[],
            all_areas_for_trend=[areas["office"]],
            respect_user_off=False,
            predictive_hvac_mode=HVACMode.COOL,
            predictive_target_temperature=72.0,
            predictive_reason="Pre-cooling before outdoor heat arrives",
        )

        assert state.rooms_need_heat is True
        assert state.rooms_need_cool is False
        assert state.target_temperature == 70.0

    def test_opposite_predictive_mode_does_not_turn_on_against_main_demand(
        self,
        controller,
        mock_hass,
    ):
        """Predictive demand cannot turn on the opposite mode during a trend mismatch."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.OFF,
            room_temperatures={
                "office": 68.0,
                "warm_room": 86.0,
            },
            target_low=70.0,
            target_high=74.0,
        )
        areas["office"].is_active = True

        state = controller.evaluate_thermostat_action(
            active_areas=[areas["office"]],
            area_temp_sensors={
                "office": ["sensor.office_temperature"],
                "warm_room": ["sensor.warm_room_temperature"],
            },
            inactive_areas=[],
            all_areas_for_trend=[areas["office"], areas["warm_room"]],
            respect_user_off=False,
            predictive_hvac_mode=HVACMode.COOL,
            predictive_target_temperature=69.0,
            predictive_reason="Pre-cooling before outdoor heat arrives",
        )

        assert state.recommended_action == ThermostatAction.NONE
        assert state.rooms_need_heat is True
        assert state.rooms_need_cool is False
        assert state.target_temperature == 72.0

    def test_opposite_predictive_mode_does_not_switch_heat_cool_main_demand(
        self,
        controller,
        mock_hass,
    ):
        """Predictive demand cannot switch heat_cool away from main room demand."""
        areas = self._configure_temperature_scenario(
            controller,
            mock_hass,
            thermostat_mode=HVACMode.HEAT_COOL,
            room_temperatures={"office": 68.0},
            target_low=70.0,
            target_high=74.0,
        )
        areas["office"].is_active = True

        state = controller.evaluate_thermostat_action(
            active_areas=[areas["office"]],
            area_temp_sensors={"office": ["sensor.office_temperature"]},
            inactive_areas=[],
            all_areas_for_trend=[areas["office"]],
            respect_user_off=False,
            predictive_hvac_mode=HVACMode.COOL,
            predictive_target_temperature=69.0,
            predictive_reason="Pre-cooling before outdoor heat arrives",
        )

        assert state.recommended_action != ThermostatAction.TURN_ON
        assert state.inferred_hvac_mode != HVACMode.COOL
        assert state.rooms_need_heat is True
        assert state.rooms_need_cool is False
