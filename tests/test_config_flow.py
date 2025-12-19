"""Tests for the config flow."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.thermostat_contact_sensors.const import (
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
    TEST_SENSOR_1,
    TEST_SENSOR_2,
    TEST_THERMOSTAT,
)


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
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
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1, TEST_SENSOR_2],
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: 5,
            CONF_CLOSE_TIMEOUT: 3,
            CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Thermostat Monitor"
    assert result["data"][CONF_CONTACT_SENSORS] == [TEST_SENSOR_1, TEST_SENSOR_2]
    assert result["data"][CONF_THERMOSTAT] == TEST_THERMOSTAT
    assert result["options"][CONF_OPEN_TIMEOUT] == 5
    assert result["options"][CONF_CLOSE_TIMEOUT] == 3
    assert result["options"][CONF_NOTIFY_SERVICE] == TEST_NOTIFY_SERVICE


async def test_config_flow_no_sensors_error(hass: HomeAssistant) -> None:
    """Test config flow with no sensors selected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Test",
            CONF_CONTACT_SENSORS: [],
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_CONTACT_SENSORS: "no_sensors_selected"}


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
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1],
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY

    # Second entry with same thermostat
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "name": "Second Entry",
            CONF_CONTACT_SENSORS: [TEST_SENSOR_2],
            CONF_THERMOSTAT: TEST_THERMOSTAT,
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",
        },
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


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
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1],
            CONF_THERMOSTAT: TEST_THERMOSTAT,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_OPEN_TIMEOUT] == DEFAULT_OPEN_TIMEOUT
    assert result["options"][CONF_CLOSE_TIMEOUT] == DEFAULT_CLOSE_TIMEOUT
    assert result["options"][CONF_NOTIFY_SERVICE] == ""


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
            CONF_CONTACT_SENSORS: [TEST_SENSOR_1],
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
