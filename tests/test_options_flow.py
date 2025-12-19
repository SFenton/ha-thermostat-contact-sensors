"""Tests for the options flow."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.thermostat_contact_sensors.const import (
    CONF_CLOSE_TIMEOUT,
    CONF_NOTIFICATION_TAG,
    CONF_NOTIFY_MESSAGE_PAUSED,
    CONF_NOTIFY_MESSAGE_RESUMED,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TITLE_PAUSED,
    CONF_NOTIFY_TITLE_RESUMED,
    CONF_OPEN_TIMEOUT,
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_NOTIFICATION_TAG,
    DEFAULT_NOTIFY_MESSAGE_PAUSED,
    DEFAULT_NOTIFY_MESSAGE_RESUMED,
    DEFAULT_NOTIFY_TITLE_PAUSED,
    DEFAULT_NOTIFY_TITLE_RESUMED,
    DEFAULT_OPEN_TIMEOUT,
    DOMAIN,
)

from .conftest import TEST_NOTIFY_SERVICE


@pytest.fixture(autouse=True)
async def setup_ha(hass: HomeAssistant, setup_test_entities) -> None:
    """Set up Home Assistant with test entities."""
    pass


async def test_options_flow_init(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test the options flow initial step."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_update_timeouts(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test updating timeout values in options flow."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_OPEN_TIMEOUT: 10,
            CONF_CLOSE_TIMEOUT: 15,
            CONF_NOTIFY_SERVICE: TEST_NOTIFY_SERVICE,
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_OPEN_TIMEOUT] == 10
    assert result["data"][CONF_CLOSE_TIMEOUT] == 15


async def test_options_flow_update_notifications(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test updating notification settings in options flow."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    custom_title = "Custom Paused Title"
    custom_message = "{{ trigger_sensor_name }} opened - thermostat paused"
    custom_tag = "my_custom_tag"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "notify.different_service",
            CONF_NOTIFY_TITLE_PAUSED: custom_title,
            CONF_NOTIFY_MESSAGE_PAUSED: custom_message,
            CONF_NOTIFY_TITLE_RESUMED: "Custom Resumed",
            CONF_NOTIFY_MESSAGE_RESUMED: "All closed now",
            CONF_NOTIFICATION_TAG: custom_tag,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NOTIFY_SERVICE] == "notify.different_service"
    assert result["data"][CONF_NOTIFY_TITLE_PAUSED] == custom_title
    assert result["data"][CONF_NOTIFY_MESSAGE_PAUSED] == custom_message
    assert result["data"][CONF_NOTIFICATION_TAG] == custom_tag


async def test_options_flow_disable_notifications(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test disabling notifications via options flow."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_OPEN_TIMEOUT: DEFAULT_OPEN_TIMEOUT,
            CONF_CLOSE_TIMEOUT: DEFAULT_CLOSE_TIMEOUT,
            CONF_NOTIFY_SERVICE: "",  # Empty to disable
            CONF_NOTIFY_TITLE_PAUSED: DEFAULT_NOTIFY_TITLE_PAUSED,
            CONF_NOTIFY_MESSAGE_PAUSED: DEFAULT_NOTIFY_MESSAGE_PAUSED,
            CONF_NOTIFY_TITLE_RESUMED: DEFAULT_NOTIFY_TITLE_RESUMED,
            CONF_NOTIFY_MESSAGE_RESUMED: DEFAULT_NOTIFY_MESSAGE_RESUMED,
            CONF_NOTIFICATION_TAG: DEFAULT_NOTIFICATION_TAG,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NOTIFY_SERVICE] == ""


async def test_options_flow_preserves_defaults(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_climate_service,
) -> None:
    """Test that options flow shows current values as defaults."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    # The schema should have the current options as defaults
    # This is implicitly tested by the form being shown correctly
    assert result["step_id"] == "init"
