"""Tests for the eco away behavior select entity."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
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
from custom_components.thermostat_contact_sensors.select import (
    EcoAwayBehavior,
    EcoAwayBehaviorSelect,
    ECO_AWAY_BEHAVIOR_LABELS,
)


# Test constants
THERMOSTAT = "climate.main_thermostat"
CONTACT_SENSOR = "binary_sensor.living_room_window"


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Thermostat",
        version=3,
        data={
            "name": "Test Thermostat Control",
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
    )


class TestEcoAwayBehaviorSelect:
    """Test the EcoAwayBehaviorSelect entity."""

    async def test_select_options(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test that select has all expected options."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.DISABLE_ECO

        select = EcoAwayBehaviorSelect(coordinator, config_entry)

        assert len(select.options) == 3
        assert ECO_AWAY_BEHAVIOR_LABELS[EcoAwayBehavior.DISABLE_ECO] in select.options
        assert ECO_AWAY_BEHAVIOR_LABELS[EcoAwayBehavior.USE_ECO_AWAY_TARGETS] in select.options
        assert ECO_AWAY_BEHAVIOR_LABELS[EcoAwayBehavior.KEEP_ECO_ACTIVE] in select.options

    async def test_current_option_returns_label(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test that current_option returns the human-readable label."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.USE_ECO_AWAY_TARGETS

        select = EcoAwayBehaviorSelect(coordinator, config_entry)

        assert select.current_option == "Use Eco Away Targets"

    async def test_select_option_updates_coordinator(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test that selecting an option updates the coordinator."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.DISABLE_ECO

        select = EcoAwayBehaviorSelect(coordinator, config_entry)
        select.hass = hass
        # Mock async_write_ha_state since entity is not fully set up in test
        select.async_write_ha_state = MagicMock()

        await select.async_select_option("Keep Eco Active")

        assert coordinator.eco_away_behavior == EcoAwayBehavior.KEEP_ECO_ACTIVE
        # Verify state was written
        select.async_write_ha_state.assert_called_once()

    async def test_default_option_is_disable_eco(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test that default option is 'Disable Eco When Away'."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.DISABLE_ECO

        select = EcoAwayBehaviorSelect(coordinator, config_entry)

        assert select.current_option == "Disable Eco When Away"

    async def test_unique_id(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test the unique_id format."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.DISABLE_ECO

        select = EcoAwayBehaviorSelect(coordinator, config_entry)

        assert select.unique_id == f"{config_entry.entry_id}_eco_away_behavior"

    async def test_icon(
        self, hass: HomeAssistant, config_entry: MockConfigEntry
    ):
        """Test the icon."""
        coordinator = MagicMock(spec=ThermostatContactSensorsCoordinator)
        coordinator.eco_away_behavior = EcoAwayBehavior.DISABLE_ECO

        select = EcoAwayBehaviorSelect(coordinator, config_entry)

        assert select.icon == "mdi:leaf-circle"


class TestEcoAwayBehaviorEnum:
    """Test the EcoAwayBehavior enum."""

    def test_enum_values(self):
        """Test that enum has expected values."""
        assert EcoAwayBehavior.DISABLE_ECO == "disable_eco_when_away"
        assert EcoAwayBehavior.USE_ECO_AWAY_TARGETS == "use_eco_away_targets"
        assert EcoAwayBehavior.KEEP_ECO_ACTIVE == "keep_eco_active"

    def test_labels_map_to_all_values(self):
        """Test that all enum values have labels."""
        for behavior in EcoAwayBehavior:
            assert behavior in ECO_AWAY_BEHAVIOR_LABELS
            assert len(ECO_AWAY_BEHAVIOR_LABELS[behavior]) > 0
