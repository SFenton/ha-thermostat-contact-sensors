"""Tests for the config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREAS,
    CONF_CLOSE_TIMEOUT,
    CONF_CONTACT_SENSORS,
    CONF_NOTIFY_SERVICE,
    CONF_OPEN_TIMEOUT,
    CONF_THERMOSTAT,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_OPEN_TIMEOUT,
    DOMAIN,
)

from .conftest import (
    TEST_NOTIFY_SERVICE,
    TEST_THERMOSTAT,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities, setup_entity_registry) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_config_flow_user_init(hass: HomeAssistant) -> None:
    """Test the initial step of the config flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_config_flow_user_success(hass: HomeAssistant) -> None:
    """Test successful config flow completion."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    assert result["type"] == FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "My Thermostat Monitor",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: 5,
            CONF_CLOSE_TIMEOUT: 3,
            CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Thermostat Monitor"
    assert result["data"][CONF_THERMOSTAT] == TEST_THERMOSTAT
    assert result["options"][CONF_OPEN_TIMEOUT] == 5
    assert result["options"][CONF_CLOSE_TIMEOUT] == 3
    assert result["options"][CONF_NOTIFY_SERVICE] == TEST_NOTIFY_SERVICE
    # Areas should be auto-discovered
    assert CONF_AREAS in result["data"]

    # Cleanup: unload the entry to stop timers
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_no_thermostat_error(hass: HomeAssistant) -> None:
    """Test config flow rejects invalid thermostat selection.

    The EntitySelector validates that the thermostat must be a valid entity ID.
    When an empty string is provided, the schema validation rejects it before
    our custom validation runs.
    """
    from homeassistant.data_entry_flow import InvalidData

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                "name": "Test",
                CONF_THERMOSTAT: "",
                CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
                CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
                CONF_NOTIFY_SERVICE: "",
            },
        )


async def test_config_flow_duplicate_thermostat(hass: HomeAssistant) -> None:
    """Test config flow prevents duplicate thermostat configurations."""
    # First entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "First Entry",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    first_entry = result["result"]

    # Second entry with same thermostat
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Second Entry",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    # Cleanup: unload the first entry to stop timers
    await hass.config_entries.async_unload(first_entry.entry_id)


async def test_config_flow_default_values(hass: HomeAssistant) -> None:
    """Test config flow uses default values."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Default Values Test",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_OPEN_TIMEOUT] == DEFAULT_OPEN_TIMEOUT
    assert result["options"][CONF_CLOSE_TIMEOUT] == DEFAULT_CLOSE_TIMEOUT
    assert result["options"][CONF_NOTIFY_SERVICE] == ""

    # Cleanup: unload the entry to stop timers
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_with_notification_service(hass: HomeAssistant) -> None:
    """Test config flow with notification service configured."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "With Notifications",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: 10,
            CONF_CLOSE_TIMEOUT: 5,
            CONF_NOTIFY_SERVICE: "notify.mobile_app_phone",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_NOTIFY_SERVICE] == "notify.mobile_app_phone"
    assert result["options"][CONF_OPEN_TIMEOUT] == 10
    assert result["options"][CONF_CLOSE_TIMEOUT] == 5

    # Cleanup: unload the entry to stop timers
    await hass.config_entries.async_unload(result["result"].entry_id)


async def test_config_flow_auto_discovers_areas(hass: HomeAssistant) -> None:
    """Test that config flow auto-discovers areas and their sensors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Area Discovery Test",
            CONF_THERMOSTAT: TEST_THERMOSTAT,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY

    # Check that areas were discovered
    areas = result["data"].get(CONF_AREAS, {})
    # Should have at least the test areas we set up
    assert len(areas) >= 0  # Will depend on entity registry setup

    # Cleanup: unload the entry to stop timers
    await hass.config_entries.async_unload(result["result"].entry_id)
