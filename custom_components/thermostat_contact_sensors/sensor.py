"""Sensor platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThermostatContactSensorsCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities = [
        OpenSensorCountSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class OpenSensorCountSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing count of open contact sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "sensors"
    _attr_icon = "mdi:door-open"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_open_count"
        self._attr_name = "Open Sensors"

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
    def native_value(self) -> int:
        """Return the count of open sensors."""
        return self.coordinator.open_count

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra state attributes."""
        coordinator: ThermostatContactSensorsCoordinator = self.coordinator

        # Get friendly names for open sensors
        open_sensor_names = []
        for sensor in coordinator.open_sensors:
            state = self.hass.states.get(sensor)
            if state:
                open_sensor_names.append(
                    state.attributes.get("friendly_name", sensor)
                )
            else:
                open_sensor_names.append(sensor)

        return {
            "open_sensors": coordinator.open_sensors,
            "open_sensor_names": open_sensor_names,
            "open_doors": coordinator.open_doors_count,
            "open_windows": coordinator.open_windows_count,
            "monitored_sensors": coordinator.contact_sensors,
            "total_monitored": len(coordinator.contact_sensors),
        }
