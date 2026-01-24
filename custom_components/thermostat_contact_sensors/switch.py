"""Switch platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import area_registry as ar

from .const import (
    CONF_AREAS,
    CONF_AREA_ENABLED,
    CONF_AREA_FORCE_TRACK_WHEN_CRITICAL,
    DOMAIN,
)
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data
    area_registry = ar.async_get(hass)

    entities = [
        RespectUserOffSwitch(coordinator, entry),
        EcoModeSwitch(coordinator, entry),
        OnlyTrackSelectedRoomsSwitch(coordinator, entry),
    ]

    # Add tracked room switches for each enabled area
    for area_id, area_config in coordinator.areas_config.items():
        if area_config.get(CONF_AREA_ENABLED, True):
            # Get area name from registry or use area_id as fallback
            area_entry = area_registry.async_get_area(area_id)
            area_name = area_entry.name if area_entry else area_id.replace("_", " ").title()
            entities.append(TrackedRoomSwitch(coordinator, entry, area_id, area_name))
            entities.append(
                ForceTrackWhenCriticalSwitch(coordinator, entry, area_id, area_name)
            )

    async_add_entities(entities)


class RespectUserOffSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control whether to respect user's manual thermostat off state.
    
    When OFF (default): Integration will always turn thermostat back on when
    windows close, even if user had manually turned it off.
    
    When ON: Integration will respect the user's choice. If the thermostat
    was off before the pause, it will stay off after windows close.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-cog"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_respect_user_off"
        self._attr_name = "Respect User Off"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            coordinator.respect_user_off = last_state.state == "on"
            _LOGGER.info(
                "Restored respect_user_off state: %s", coordinator.respect_user_off
            )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def is_on(self) -> bool:
        """Return True if respecting user's off state."""
        return self.coordinator.respect_user_off

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on - respect user's manual off choice."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.respect_user_off = True
        _LOGGER.info("Respect user off enabled - will not override manual off state")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off - always resume thermostat when windows close."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.respect_user_off = False
        _LOGGER.info("Respect user off disabled - will always resume thermostat")
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "description": (
                "When ON: Respects user's choice to keep thermostat off. "
                "When OFF: Always resumes thermostat when windows close."
            ),
        }


class EcoModeSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control eco mode for thermostat control.
    
    When OFF (default): Thermostat activates based on all rooms including
    unoccupied rooms with critical temperatures.
    
    When ON: Thermostat only activates based on active (occupied) rooms.
    Unoccupied rooms will not trigger thermostat activation, even if they
    reach critical temperatures.

    The "Eco Mode Critical Tracking" select controls *how* eco treats inactive
    critical rooms when eco is enabled.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:leaf"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_eco_mode"
        self._attr_name = "Eco Mode"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            coordinator.eco_mode = last_state.state == "on"
            _LOGGER.info(
                "Restored eco_mode state: %s",
                coordinator.eco_mode,
            )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def is_on(self) -> bool:
        """Return True if eco mode is enabled."""
        return bool(getattr(self.coordinator, "eco_mode", False))

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on eco mode - only consider active rooms."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.eco_mode = True
        _LOGGER.info("Eco mode enabled")
        self.async_write_ha_state()
        # Trigger coordinator update to re-evaluate thermostat state
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off eco mode - consider all rooms including critical unoccupied ones."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.eco_mode = False
        _LOGGER.info("Eco mode disabled")
        self.async_write_ha_state()
        # Trigger coordinator update to re-evaluate thermostat state
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "description": (
                "When ON: Thermostat only activates for active (occupied) rooms. "
                "When OFF: Thermostat also activates for unoccupied rooms with critical temperatures. "
                "Use 'Eco Mode Critical Tracking' select to control critical-room behavior while eco is ON."
            ),
            "eco_mode_critical_tracking": getattr(self.coordinator, "eco_mode_critical_tracking", None),
        }


class OnlyTrackSelectedRoomsSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control whether to only heat/cool selected/tracked rooms.
    
    When OFF (default): All rooms are considered for heating/cooling decisions.
    All monitored areas participate in thermostat control.
    
    When ON: Only rooms that have their individual "Track [Room]" switch enabled
    will be considered for heating/cooling decisions. Untracked rooms will be
    ignored even if they need conditioning. Anomaly detection still applies -
    if a tracked room needs cooling but the whole house trends towards heat,
    we won't cool.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_only_track_selected_rooms"
        self._attr_name = "Only Track Selected Rooms"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring state for %s: %s", self.entity_id, last_state.state
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            coordinator.only_track_selected_rooms = last_state.state == "on"
            _LOGGER.info(
                "Restored only_track_selected_rooms state: %s",
                coordinator.only_track_selected_rooms,
            )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def is_on(self) -> bool:
        """Return True if only tracking selected rooms."""
        return self.coordinator.only_track_selected_rooms

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on - only consider tracked rooms for heating/cooling."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.only_track_selected_rooms = True
        _LOGGER.info(
            "Only track selected rooms enabled - thermostat will only consider tracked rooms "
            "(tracked: %s)",
            coordinator.tracked_rooms,
        )
        self.async_write_ha_state()
        # Trigger coordinator update to re-evaluate thermostat state
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off - consider all rooms for heating/cooling."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.only_track_selected_rooms = False
        _LOGGER.info("Only track selected rooms disabled - thermostat will consider all rooms")
        self.async_write_ha_state()
        # Trigger coordinator update to re-evaluate thermostat state
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        return {
            "tracked_rooms": list(coordinator.tracked_rooms),
            "tracked_room_count": len(coordinator.tracked_rooms),
            "total_room_count": len(coordinator.all_enabled_area_ids),
            "description": (
                "When ON: Only rooms with 'Track [Room]' enabled will be heated/cooled. "
                "When OFF: All rooms are considered for heating/cooling decisions."
            ),
        }


class TrackedRoomSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to control whether a specific room is tracked for heating/cooling.
    
    When ON: This room is included in heating/cooling decisions when
    "Only Track Selected Rooms" is enabled.
    
    When OFF: This room is excluded from heating/cooling decisions when
    "Only Track Selected Rooms" is enabled.
    
    Note: This switch only has effect when "Only Track Selected Rooms" is ON.
    When that feature is OFF, all rooms are considered regardless of this switch.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermometer-check"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
        area_id: str,
        area_name: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._area_id = area_id
        self._area_name = area_name
        self._attr_unique_id = f"{entry.entry_id}_track_room_{area_id}"
        self._attr_name = f"Track {area_name}"

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            _LOGGER.debug(
                "Restoring tracked room state for %s: %s",
                self._area_id,
                last_state.state,
            )
            coordinator: ThermostatContactSensorsCoordinator = self.coordinator
            # Restore tracked state - if it was on, add to tracked rooms
            if last_state.state == "on":
                coordinator.set_room_tracked(self._area_id, True)
                _LOGGER.info(
                    "Restored tracked room state: area=%s, tracked=True",
                    self._area_id,
                )

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    @property
    def is_on(self) -> bool:
        """Return True if this room is being tracked."""
        return self._area_id in self.coordinator.tracked_rooms

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on - add this room to tracked rooms."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.set_room_tracked(self._area_id, True)
        _LOGGER.info("Room %s is now tracked for heating/cooling", self._area_name)
        self.async_write_ha_state()
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off - remove this room from tracked rooms."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        coordinator.set_room_tracked(self._area_id, False)
        _LOGGER.info("Room %s is no longer tracked for heating/cooling", self._area_name)
        self.async_write_ha_state()
        self.hass.async_create_task(coordinator.async_update_thermostat_state())

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator
        return {
            "area_id": self._area_id,
            "area_name": self._area_name,
            "only_track_selected_rooms_enabled": coordinator.only_track_selected_rooms,
            "effective": coordinator.only_track_selected_rooms,
            "description": (
                f"When ON: {self._area_name} is included in heating/cooling decisions. "
                "This only takes effect when 'Only Track Selected Rooms' is enabled."
            ),
        }


class ForceTrackWhenCriticalSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Per-area override: always track the room when it's critical.

    This is the per-room override used by Eco Mode Critical Tracking = "Track Select Critical".
    When enabled for a room, that room will still be considered for critical-temperature
    protection even if it's inactive and Eco is enabled (depending on policy).
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-decagram"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
        area_id: str,
        area_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._area_id = area_id
        self._area_name = area_name
        self._attr_unique_id = f"{entry.entry_id}_{area_id}_force_track_when_critical"
        self._attr_name = f"{area_name} Force Track When Critical"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    def _get_current_value(self) -> bool:
        area_config = self.coordinator.areas_config.get(self._area_id, {})
        return bool(area_config.get(CONF_AREA_FORCE_TRACK_WHEN_CRITICAL, False))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Prefer restored switch state, falling back to config entry value.
        if (last_state := await self.async_get_last_state()) is not None:
            desired = last_state.state == "on"
            await self._apply_value(desired, trigger_update=False)

    @property
    def is_on(self) -> bool:
        return self._get_current_value()

    async def async_turn_on(self, **kwargs) -> None:
        await self._apply_value(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._apply_value(False)

    async def _apply_value(self, enabled: bool, *, trigger_update: bool = True) -> None:
        """Persist the override and optionally trigger re-evaluation."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        # Update in-memory config immediately so UI reflects the change without waiting for reload.
        coordinator.areas_config.setdefault(self._area_id, {})[
            CONF_AREA_FORCE_TRACK_WHEN_CRITICAL
        ] = enabled

        # Persist to config entry data so it survives restarts.
        areas_config = dict(self._entry.data.get(CONF_AREAS, {}))
        area_cfg = dict(areas_config.get(self._area_id, {}))
        area_cfg[CONF_AREA_FORCE_TRACK_WHEN_CRITICAL] = enabled
        areas_config[self._area_id] = area_cfg

        new_data = {**self._entry.data, CONF_AREAS: areas_config}
        if self.hass is not None:
            self.hass.config_entries.async_update_entry(self._entry, data=new_data)

        if self.entity_id is not None:
            self.async_write_ha_state()

        if trigger_update:
            self.hass.async_create_task(coordinator.async_update_thermostat_state())

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "area_id": self._area_id,
            "area_name": self._area_name,
            "description": (
                "When ON: This room is always included for critical-temperature tracking when applicable. "
                "Used by 'Eco Mode Critical Tracking' = 'Track Select Critical'."
            ),
        }
