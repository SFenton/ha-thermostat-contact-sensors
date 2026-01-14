"""Tests for the respect_user_off switch and behavior."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREA_ENABLED,
    CONF_AREA_ID,
    CONF_AREAS,
    CONF_BINARY_SENSORS,
    CONF_CONTACT_SENSORS,
    CONF_THERMOSTAT,
    DOMAIN,
)
from custom_components.thermostat_contact_sensors.coordinator import (
    ThermostatContactSensorsCoordinator,
)
from custom_components.thermostat_contact_sensors.switch import RespectUserOffSwitch


# Test constants
THERMOSTAT = "climate.main_thermostat"
CONTACT_SENSOR = "binary_sensor.living_room_window"


def get_contact_sensors_from_areas(areas_config: dict) -> list[str]:
    """Extract all contact sensors from areas config."""
    contact_sensors = []
    for area_id, area_config in areas_config.items():
        if area_config.get(CONF_AREA_ENABLED, True):
            area_contact_sensors = area_config.get(CONF_CONTACT_SENSORS, [])
            contact_sensors.extend(area_contact_sensors)
    return contact_sensors


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Thermostat",
        version=3,
        data={
            CONF_THERMOSTAT: THERMOSTAT,
            CONF_AREAS: {
                "living_room": {
                    CONF_AREA_ID: "living_room",
                    CONF_AREA_ENABLED: True,
                    CONF_CONTACT_SENSORS: [CONTACT_SENSOR],
                    CONF_BINARY_SENSORS: [],
                }
            },
        },
        options={},
    )


@pytest.fixture
async def setup_entities(hass: HomeAssistant) -> None:
    """Set up the test entities."""
    # Create thermostat entity
    hass.states.async_set(
        THERMOSTAT,
        HVACMode.HEAT,
        {
            "friendly_name": "Main Thermostat",
            "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
            "current_temperature": 20,
            "temperature": 22,
        },
    )

    # Create contact sensor entity (closed initially)
    hass.states.async_set(
        CONTACT_SENSOR,
        STATE_OFF,
        {"device_class": "window", "friendly_name": "Living Room Window"},
    )

    await hass.async_block_till_done()


class TestRespectUserOffSwitch:
    """Test the RespectUserOffSwitch entity."""

    @pytest.mark.asyncio
    async def test_switch_default_state_is_off(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test that the switch defaults to off (always resume)."""
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Default should be False (always resume)
        assert coordinator.respect_user_off is False

        switch = RespectUserOffSwitch(coordinator, config_entry)
        assert switch.is_on is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_turn_on(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test turning the switch on."""
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Test directly on coordinator instead of through switch
        # (switch.async_turn_on calls async_write_ha_state which requires entity registration)
        assert coordinator.respect_user_off is False
        coordinator.respect_user_off = True
        assert coordinator.respect_user_off is True

        # Also verify switch reflects the state
        switch = RespectUserOffSwitch(coordinator, config_entry)
        assert switch.is_on is True

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_turn_off(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test turning the switch off."""
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Test directly on coordinator instead of through switch
        # (switch methods call async_write_ha_state which requires entity registration)
        coordinator.respect_user_off = True
        assert coordinator.respect_user_off is True

        coordinator.respect_user_off = False
        assert coordinator.respect_user_off is False

        # Also verify switch reflects the state
        switch = RespectUserOffSwitch(coordinator, config_entry)
        assert switch.is_on is False

        await coordinator.async_shutdown()


class TestRespectUserOffBehavior:
    """Test the thermostat behavior with respect_user_off setting."""

    @pytest.fixture
    def mock_climate_service(self, hass: HomeAssistant) -> dict:
        """Mock the climate.set_hvac_mode service."""
        calls = {"set_hvac_mode": []}

        async def mock_set_hvac_mode(service_call):
            calls["set_hvac_mode"].append(dict(service_call.data))
            # Update state to reflect mode change
            mode = service_call.data.get("hvac_mode")
            hass.states.async_set(
                THERMOSTAT,
                mode,
                {
                    "friendly_name": "Main Thermostat",
                    "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
                    "current_temperature": 20,
                    "temperature": 22,
                },
            )

        hass.services.async_register("climate", "set_hvac_mode", mock_set_hvac_mode)
        return calls

    @pytest.mark.asyncio
    async def test_thermostat_off_resumes_when_respect_off_disabled(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
        mock_climate_service: dict,
    ):
        """Test that thermostat resumes even if it was off, when respect_user_off is False."""
        # Start with thermostat OFF
        hass.states.async_set(
            THERMOSTAT,
            HVACMode.OFF,
            {
                "friendly_name": "Main Thermostat",
                "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
                "current_temperature": 20,
                "temperature": 22,
            },
        )
        await hass.async_block_till_done()

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Ensure respect_user_off is disabled
        coordinator.respect_user_off = False
        # Set a last known active mode
        coordinator._last_known_hvac_mode = HVACMode.HEAT

        # Open contact and trigger pause
        hass.states.async_set(CONTACT_SENSOR, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == HVACMode.OFF

        # Clear the mock calls from the pause
        mock_climate_service["set_hvac_mode"].clear()

        # Close contact and trigger resume
        hass.states.async_set(CONTACT_SENSOR, STATE_OFF)
        await hass.async_block_till_done()
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        # Should have resumed to last known mode (HEAT)
        assert coordinator.is_paused is False
        assert len(mock_climate_service["set_hvac_mode"]) == 1
        assert mock_climate_service["set_hvac_mode"][0]["hvac_mode"] == HVACMode.HEAT

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_thermostat_stays_off_when_respect_off_enabled(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
        mock_climate_service: dict,
    ):
        """Test that thermostat stays off when respect_user_off is True."""
        # Start with thermostat OFF
        hass.states.async_set(
            THERMOSTAT,
            HVACMode.OFF,
            {
                "friendly_name": "Main Thermostat",
                "hvac_modes": [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO],
                "current_temperature": 20,
                "temperature": 22,
            },
        )
        await hass.async_block_till_done()

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Enable respect_user_off
        coordinator.respect_user_off = True

        # Open contact and trigger pause
        hass.states.async_set(CONTACT_SENSOR, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == HVACMode.OFF

        # Clear the mock calls from the pause
        mock_climate_service["set_hvac_mode"].clear()

        # Close contact and trigger resume
        hass.states.async_set(CONTACT_SENSOR, STATE_OFF)
        await hass.async_block_till_done()
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        # Should NOT have called set_hvac_mode - thermostat stays off
        assert coordinator.is_paused is False
        assert len(mock_climate_service["set_hvac_mode"]) == 0

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_active_thermostat_always_resumes(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
        mock_climate_service: dict,
    ):
        """Test that thermostat always resumes when it was on before (regardless of setting)."""
        # Thermostat is HEAT (from setup_entities)
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Enable respect_user_off (shouldn't matter for active thermostat)
        coordinator.respect_user_off = True

        # Open contact and trigger pause
        hass.states.async_set(CONTACT_SENSOR, STATE_ON)
        await hass.async_block_till_done()
        await coordinator._async_open_timeout_expired()
        await hass.async_block_till_done()

        assert coordinator.is_paused is True
        assert coordinator.previous_hvac_mode == HVACMode.HEAT

        # Clear the mock calls from the pause
        mock_climate_service["set_hvac_mode"].clear()

        # Close contact and trigger resume
        hass.states.async_set(CONTACT_SENSOR, STATE_OFF)
        await hass.async_block_till_done()
        await coordinator._async_close_timeout_expired()
        await hass.async_block_till_done()

        # Should have resumed to HEAT
        assert coordinator.is_paused is False
        assert len(mock_climate_service["set_hvac_mode"]) == 1
        assert mock_climate_service["set_hvac_mode"][0]["hvac_mode"] == HVACMode.HEAT

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_extra_state_attributes(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test that the switch has proper extra state attributes."""
        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        switch = RespectUserOffSwitch(coordinator, config_entry)
        
        attrs = switch.extra_state_attributes
        assert "description" in attrs
        assert "respect" in attrs["description"].lower()

        await coordinator.async_shutdown()


class TestEcoModeSwitch:
    """Test the EcoModeSwitch entity."""

    @pytest.mark.asyncio
    async def test_switch_default_state_is_off(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test that the eco mode switch defaults to off."""
        from custom_components.thermostat_contact_sensors.switch import EcoModeSwitch

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Default should be False (consider all rooms)
        assert coordinator.eco_mode is False

        switch = EcoModeSwitch(coordinator, config_entry)
        assert switch.is_on is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_turn_on(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test turning the eco mode switch on."""
        from custom_components.thermostat_contact_sensors.switch import EcoModeSwitch

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Test directly on coordinator
        assert coordinator.eco_mode is False
        coordinator.eco_mode = True
        assert coordinator.eco_mode is True

        # Verify switch reflects the state
        switch = EcoModeSwitch(coordinator, config_entry)
        assert switch.is_on is True

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_turn_off(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test turning the eco mode switch off."""
        from custom_components.thermostat_contact_sensors.switch import EcoModeSwitch

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        # Enable then disable
        coordinator.eco_mode = True
        assert coordinator.eco_mode is True

        coordinator.eco_mode = False
        assert coordinator.eco_mode is False

        # Verify switch reflects the state
        switch = EcoModeSwitch(coordinator, config_entry)
        assert switch.is_on is False

        await coordinator.async_shutdown()

    @pytest.mark.asyncio
    async def test_switch_has_correct_attributes(
        self,
        hass: HomeAssistant,
        config_entry: MockConfigEntry,
        setup_entities: None,
    ):
        """Test that the switch has correct attributes."""
        from custom_components.thermostat_contact_sensors.switch import EcoModeSwitch

        config_entry.add_to_hass(hass)

        coordinator = ThermostatContactSensorsCoordinator(
            hass=hass,
            config_entry_id=config_entry.entry_id,
            contact_sensors=get_contact_sensors_from_areas(config_entry.data[CONF_AREAS]),
            thermostat=THERMOSTAT,
            options=config_entry.options,
            areas_config=config_entry.data[CONF_AREAS],
        )
        await coordinator.async_setup()

        switch = EcoModeSwitch(coordinator, config_entry)

        assert switch.name == "Eco Mode"
        assert switch.icon == "mdi:leaf"
        assert "eco_mode" in switch.unique_id

        attrs = switch.extra_state_attributes
        assert "description" in attrs
        assert "active" in attrs["description"].lower() or "occupied" in attrs["description"].lower()

        await coordinator.async_shutdown()
