"""Tests for integration services."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermostat_contact_sensors import (
    ATTR_ENTRY_ID,
    SERVICE_PAUSE,
    SERVICE_RECALCULATE,
    SERVICE_RESUME,
)
from custom_components.thermostat_contact_sensors.const import DOMAIN

from .conftest import TEST_THERMOSTAT


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


class TestServiceRegistration:
    """Tests for service registration."""

    async def test_services_registered(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that services are registered when integration loads."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify services are registered
        assert hass.services.has_service(DOMAIN, SERVICE_PAUSE)
        assert hass.services.has_service(DOMAIN, SERVICE_RESUME)
        assert hass.services.has_service(DOMAIN, SERVICE_RECALCULATE)

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_services_registered_once(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that services are only registered once even with multiple entries."""
        # Create a second config entry
        hass.states.async_set(
            "climate.second_thermostat",
            "cool",
            {"friendly_name": "Second Thermostat"},
        )
        await hass.async_block_till_done()

        second_entry = MockConfigEntry(
            domain=DOMAIN,
            title="Second Config",
            version=2,
            data={
                "name": "Second Config",
                "contact_sensors": ["binary_sensor.front_door_contact"],
                "thermostat": "climate.second_thermostat",
                "areas": {},
            },
            options={
                "min_occupancy_minutes": 5,
                "temperature_deadband": 0.5,
                "min_cycle_on_minutes": 5,
                "min_cycle_off_minutes": 5,
                "open_timeout": 5,
                "close_timeout": 5,
                "notify_service": "",
                "notify_title_paused": "Paused",
                "notify_message_paused": "Paused",
                "notify_title_resumed": "Resumed",
                "notify_message_resumed": "Resumed",
                "notification_tag": "thermostat",
            },
            entry_id="second_entry_id",
            unique_id="climate.second_thermostat",
        )

        # Set up both entries
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        second_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

        # Services should still be available
        assert hass.services.has_service(DOMAIN, SERVICE_PAUSE)
        assert hass.services.has_service(DOMAIN, SERVICE_RESUME)
        assert hass.services.has_service(DOMAIN, SERVICE_RECALCULATE)

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.config_entries.async_unload(second_entry.entry_id)


class TestPauseService:
    """Tests for the pause service."""

    async def test_pause_service_pauses_thermostat(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that pause service pauses the thermostat."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data
        assert coordinator.is_paused is False

        # Call pause service
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PAUSE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Verify thermostat is paused
        assert coordinator.is_paused is True

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_pause_service_already_paused(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that pause service does nothing if already paused."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data

        # Pause first time
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PAUSE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert coordinator.is_paused is True

        # Pause again - should not raise
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PAUSE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert coordinator.is_paused is True

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_pause_service_invalid_entry_id(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that pause service raises error for invalid entry ID."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_PAUSE,
                {ATTR_ENTRY_ID: "invalid_entry_id"},
                blocking=True,
            )

        await hass.config_entries.async_unload(mock_config_entry.entry_id)


class TestResumeService:
    """Tests for the resume service."""

    async def test_resume_service_resumes_thermostat(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that resume service resumes the thermostat."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data

        # First pause
        await hass.services.async_call(
            DOMAIN,
            SERVICE_PAUSE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert coordinator.is_paused is True

        # Then resume
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESUME,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Verify thermostat is resumed
        assert coordinator.is_paused is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_resume_service_not_paused(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that resume service does nothing if not paused."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        coordinator = mock_config_entry.runtime_data
        assert coordinator.is_paused is False

        # Resume when not paused - should not raise
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESUME,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert coordinator.is_paused is False

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_resume_service_invalid_entry_id(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that resume service raises error for invalid entry ID."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RESUME,
                {ATTR_ENTRY_ID: "invalid_entry_id"},
                blocking=True,
            )

        await hass.config_entries.async_unload(mock_config_entry.entry_id)


class TestRecalculateService:
    """Tests for the recalculate service."""

    async def test_recalculate_service(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that recalculate service triggers state recalculation."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # Call recalculate service - should not raise
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RECALCULATE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        await hass.async_block_till_done()

        await hass.config_entries.async_unload(mock_config_entry.entry_id)

    async def test_recalculate_service_invalid_entry_id(
        self,
        hass: HomeAssistant,
        mock_config_entry: ConfigEntry,
        mock_climate_service,
    ) -> None:
        """Test that recalculate service raises error for invalid entry ID."""
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_RECALCULATE,
                {ATTR_ENTRY_ID: "invalid_entry_id"},
                blocking=True,
            )

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
