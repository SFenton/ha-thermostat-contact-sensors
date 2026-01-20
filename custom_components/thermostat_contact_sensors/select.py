"""Select platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging
from enum import StrEnum

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)


class EcoAwayBehavior(StrEnum):
    """Eco mode behavior options when away."""

    DISABLE_ECO = "disable_eco_when_away"
    USE_ECO_AWAY_TARGETS = "use_eco_away_targets"
    KEEP_ECO_ACTIVE = "keep_eco_active"


# Human-readable labels for the options
ECO_AWAY_BEHAVIOR_LABELS = {
    EcoAwayBehavior.DISABLE_ECO: "Disable Eco When Away",
    EcoAwayBehavior.USE_ECO_AWAY_TARGETS: "Use Eco Away Targets",
    EcoAwayBehavior.KEEP_ECO_ACTIVE: "Keep Eco Active",
}

# Reverse mapping for lookup
ECO_AWAY_BEHAVIOR_BY_LABEL = {v: k for k, v in ECO_AWAY_BEHAVIOR_LABELS.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities = [
        EcoAwayBehaviorSelect(coordinator, entry),
    ]

    async_add_entities(entities)


class EcoAwayBehaviorSelect(CoordinatorEntity, RestoreEntity, SelectEntity):
    """Select entity for configuring eco mode behavior when away.
    
    Options:
    - Disable Eco When Away: Reverts to normal behavior (respects global vTherm,
      critical rooms, away buffer values as usual).
    - Use Eco Away Targets: Uses the "Eco Away" virtual thermostat for targets,
      still respects away buffer values from config.
    - Keep Eco Active: Keeps eco mode active when away. Will effectively not
      heat/cool the home while away (no active rooms = no conditioning).
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:leaf-circle"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_eco_away_behavior"
        self._attr_name = "Eco Behavior When Away"
        self._attr_options = list(ECO_AWAY_BEHAVIOR_LABELS.values())

    async def async_added_to_hass(self) -> None:
        """Restore state when added to hass."""
        await super().async_added_to_hass()

        # Try to restore previous state
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state in self._attr_options:
                # Convert label back to enum value
                if last_state.state in ECO_AWAY_BEHAVIOR_BY_LABEL:
                    behavior = ECO_AWAY_BEHAVIOR_BY_LABEL[last_state.state]
                    self.coordinator.eco_away_behavior = behavior
                    _LOGGER.info(
                        "Restored eco_away_behavior state: %s", behavior
                    )
                else:
                    _LOGGER.debug(
                        "Could not restore eco_away_behavior, using default: %s",
                        last_state.state
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
    def current_option(self) -> str:
        """Return the currently selected option."""
        behavior = self.coordinator.eco_away_behavior
        return ECO_AWAY_BEHAVIOR_LABELS.get(behavior, ECO_AWAY_BEHAVIOR_LABELS[EcoAwayBehavior.DISABLE_ECO])

    async def async_select_option(self, option: str) -> None:
        """Handle option selection."""
        if option in ECO_AWAY_BEHAVIOR_BY_LABEL:
            behavior = ECO_AWAY_BEHAVIOR_BY_LABEL[option]
            self.coordinator.eco_away_behavior = behavior
            _LOGGER.info("Eco away behavior set to: %s", behavior)
            self.async_write_ha_state()
            # Trigger coordinator update to re-evaluate thermostat state
            self.hass.async_create_task(self.coordinator.async_update_thermostat_state())

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        return {
            "description": (
                "Configures how eco mode behaves when everyone is away. "
                "'Disable Eco When Away' reverts to normal behavior with critical room protection. "
                "'Use Eco Away Targets' uses the Eco Away thermostat targets. "
                "'Keep Eco Active' will not heat/cool while away (energy savings, but no protection)."
            ),
        }
