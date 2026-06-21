"""Tests for per-area Track Only When Occupied switches."""
from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.thermostat_contact_sensors.const import (
    CONF_AREAS,
    CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED,
    DOMAIN,
)


async def test_track_only_when_occupied_switch_persists_area_config(
    hass: HomeAssistant,
    mock_config_entry,
    mock_climate_service,
    setup_test_entities,
) -> None:
    """Test per-area switch updates runtime and stored config."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "switch",
        DOMAIN,
        f"{mock_config_entry.entry_id}_living_room_track_only_when_occupied",
    )

    assert entity_id is not None
    assert hass.states.get(entity_id).state == STATE_OFF

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_ON
    assert (
        mock_config_entry.runtime_data.areas_config["living_room"][
            CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED
        ]
        is True
    )
    assert (
        mock_config_entry.data[CONF_AREAS]["living_room"][
            CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED
        ]
        is True
    )

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_OFF
    assert (
        mock_config_entry.runtime_data.areas_config["living_room"][
            CONF_AREA_TRACK_ONLY_WHEN_OCCUPIED
        ]
        is False
    )

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
