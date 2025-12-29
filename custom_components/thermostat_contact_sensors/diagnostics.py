"""Diagnostics support for Thermostat Contact Sensors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_AREAS,
    CONF_CONTACT_SENSORS,
    CONF_NOTIFY_SERVICE,
    CONF_THERMOSTAT,
    DOMAIN,
)
from .coordinator import ThermostatContactSensorsCoordinator

# Keys to redact from diagnostics
TO_REDACT = {
    CONF_NOTIFY_SERVICE,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    # Get entity registry to gather entity information
    entity_reg = er.async_get(hass)

    # Get entities created by this integration
    entities = []
    for entity in entity_reg.entities.values():
        if entity.config_entry_id == entry.entry_id:
            state = hass.states.get(entity.entity_id)
            entities.append({
                "entity_id": entity.entity_id,
                "unique_id": entity.unique_id,
                "disabled": entity.disabled,
                "state": state.state if state else None,
                "attributes": dict(state.attributes) if state else None,
            })

    # Get thermostat state
    thermostat_state = hass.states.get(coordinator.thermostat)
    thermostat_info = None
    if thermostat_state:
        thermostat_info = {
            "entity_id": coordinator.thermostat,
            "state": thermostat_state.state,
            "attributes": {
                "hvac_modes": thermostat_state.attributes.get("hvac_modes"),
                "current_temperature": thermostat_state.attributes.get("current_temperature"),
                "temperature": thermostat_state.attributes.get("temperature"),
                "target_temp_high": thermostat_state.attributes.get("target_temp_high"),
                "target_temp_low": thermostat_state.attributes.get("target_temp_low"),
            },
        }

    # Get contact sensor states
    contact_sensors = []
    for sensor_id in coordinator.contact_sensors:
        state = hass.states.get(sensor_id)
        contact_sensors.append({
            "entity_id": sensor_id,
            "state": state.state if state else None,
            "attributes": {
                "friendly_name": state.attributes.get("friendly_name") if state else None,
                "device_class": state.attributes.get("device_class") if state else None,
            } if state else None,
        })

    # Get occupancy tracker state
    occupancy_state = {}
    for area_id, area_state in coordinator.occupancy_tracker.areas.items():
        occupancy_state[area_id] = {
            "area_name": area_state.area_name,
            "is_occupied": area_state.is_occupied,
            "is_active": area_state.is_active,
            "occupied_sensor_count": area_state.occupied_sensor_count,
            "total_sensor_count": area_state.total_sensor_count,
            "occupancy_minutes": area_state.get_occupancy_minutes(),
            "is_in_grace_period": area_state.is_in_grace_period,
            "binary_sensors": area_state.binary_sensors,
            "sensors": area_state.sensors,
            "occupied_binary_sensors": list(area_state.occupied_binary_sensors),
            "occupied_sensors": list(area_state.occupied_sensors),
        }

    # Get thermostat control state
    thermostat_control_state = None
    if coordinator.last_thermostat_state:
        ts = coordinator.last_thermostat_state
        room_states = {}
        for area_id, room_state in ts.room_states.items():
            room_states[area_id] = {
                "area_name": room_state.area_name,
                "is_active": room_state.is_active,
                "is_satiated": room_state.is_satiated,
                "satiation_reason": room_state.satiation_reason.value if room_state.satiation_reason else None,
                "is_critical": room_state.is_critical,
                "critical_reason": room_state.critical_reason,
                "temperature_sensors": room_state.temperature_sensors,
                "sensor_readings": room_state.sensor_readings,
                "determining_sensor": room_state.determining_sensor,
                "determining_temperature": room_state.determining_temperature,
                "target_temperature": room_state.target_temperature,
            }

        thermostat_control_state = {
            "hvac_mode": ts.hvac_mode.value if ts.hvac_mode else None,
            "is_on": ts.is_on,
            "target_temperature": ts.target_temperature,
            "target_temp_low": ts.target_temp_low,
            "target_temp_high": ts.target_temp_high,
            "active_room_count": ts.active_room_count,
            "satiated_room_count": ts.satiated_room_count,
            "all_active_rooms_satiated": ts.all_active_rooms_satiated,
            "critical_room_count": ts.critical_room_count,
            "recommended_action": ts.recommended_action.value if ts.recommended_action else None,
            "action_reason": ts.action_reason,
            "room_states": room_states,
        }

    # Get vent control state
    vent_control_state = None
    if coordinator.last_vent_control_state:
        vcs = coordinator.last_vent_control_state
        area_states = {}
        for area_id, area_state in vcs.area_states.items():
            vents = []
            for vent in area_state.vents:
                vents.append({
                    "entity_id": vent.entity_id,
                    "is_group": vent.is_group,
                    "member_count": vent.member_count,
                    "is_open": vent.is_open,
                    "should_be_open": vent.should_be_open,
                    "open_reason": vent.open_reason,
                })
            area_states[area_id] = {
                "area_name": area_state.area_name,
                "total_vent_count": area_state.total_vent_count,
                "open_vent_count": area_state.open_vent_count,
                "should_open": area_state.should_open,
                "open_reason": area_state.open_reason,
                "distance_from_target": area_state.distance_from_target,
                "vents": vents,
            }

        vent_control_state = {
            "total_vents": vcs.total_vents,
            "open_vents": vcs.open_vents,
            "vents_should_be_open": vcs.vents_should_be_open,
            "pending_commands": [
                {"entity_id": cmd[0], "should_open": cmd[1], "reason": cmd[2]}
                for cmd in vcs.pending_commands
            ],
            "area_states": area_states,
        }

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator_state": {
            "is_paused": coordinator.is_paused,
            "previous_hvac_mode": coordinator.previous_hvac_mode,
            "open_sensors": coordinator.open_sensors,
            "open_count": coordinator.open_count,
            "open_doors_count": coordinator.open_doors_count,
            "open_windows_count": coordinator.open_windows_count,
            "trigger_sensor": coordinator.trigger_sensor,
            "respect_user_off": coordinator.respect_user_off,
            "open_timeout": coordinator.open_timeout,
            "close_timeout": coordinator.close_timeout,
        },
        "thermostat": thermostat_info,
        "contact_sensors": contact_sensors,
        "occupancy_state": occupancy_state,
        "thermostat_control_state": thermostat_control_state,
        "vent_control_state": vent_control_state,
        "entities": entities,
    }
