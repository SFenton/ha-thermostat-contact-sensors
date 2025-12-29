"""Tests for diagnostics support."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.thermostat_contact_sensors.const import DOMAIN
from custom_components.thermostat_contact_sensors.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_SENSOR_3,
    TEST_THERMOSTAT,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


class TestDiagnostics:
    """Tests for diagnostics output."""

    async def test_diagnostics_returns_data(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that diagnostics returns expected data structure."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        # Verify top-level keys
        assert "config_entry" in diagnostics
        assert "coordinator_state" in diagnostics
        assert "thermostat" in diagnostics
        assert "contact_sensors" in diagnostics
        assert "occupancy_state" in diagnostics
        assert "entities" in diagnostics

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_config_entry_info(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics config entry info."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        config_entry = diagnostics["config_entry"]
        assert config_entry["entry_id"] == mock_config_entry.entry_id
        assert config_entry["version"] == 2
        assert config_entry["title"] == mock_config_entry.title

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_coordinator_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics coordinator state info."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        coord_state = diagnostics["coordinator_state"]
        assert "is_paused" in coord_state
        assert "previous_hvac_mode" in coord_state
        assert "open_sensors" in coord_state
        assert "open_count" in coord_state
        assert "trigger_sensor" in coord_state
        assert "respect_user_off" in coord_state
        assert "open_timeout" in coord_state
        assert "close_timeout" in coord_state

        # Default state should be not paused with no open sensors
        assert coord_state["is_paused"] is False
        assert coord_state["open_sensors"] == []
        assert coord_state["open_count"] == 0

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_thermostat_info(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics thermostat info."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        thermostat = diagnostics["thermostat"]
        assert thermostat is not None
        assert thermostat["entity_id"] == TEST_THERMOSTAT
        assert "state" in thermostat
        assert "attributes" in thermostat

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_contact_sensors(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics contact sensor info."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        contact_sensors = diagnostics["contact_sensors"]
        assert len(contact_sensors) == 3

        # Check sensor structure
        for sensor in contact_sensors:
            assert "entity_id" in sensor
            assert "state" in sensor
            assert "attributes" in sensor

        # Verify our test sensors are included
        sensor_ids = [s["entity_id"] for s in contact_sensors]
        assert TEST_SENSOR_1 in sensor_ids
        assert TEST_SENSOR_2 in sensor_ids
        assert TEST_SENSOR_3 in sensor_ids

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_entities_list(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics entities list."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        entities = diagnostics["entities"]
        # We should have at least the binary_sensor and sensor entities
        assert len(entities) >= 2

        # Check entity structure
        for entity in entities:
            assert "entity_id" in entity
            assert "unique_id" in entity

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_redacts_notify_service(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that notify service is redacted from diagnostics."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        # Check that notify_service in options is redacted
        options = diagnostics["config_entry"]["options"]
        if "notify_service" in options:
            assert options["notify_service"] == "**REDACTED**"

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_diagnostics_occupancy_state(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test diagnostics occupancy state info."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

        occupancy_state = diagnostics["occupancy_state"]
        assert isinstance(occupancy_state, dict)

        # Check area structure if any areas are configured
        for area_id, area_state in occupancy_state.items():
            assert "area_name" in area_state
            assert "is_occupied" in area_state
            assert "is_active" in area_state

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
