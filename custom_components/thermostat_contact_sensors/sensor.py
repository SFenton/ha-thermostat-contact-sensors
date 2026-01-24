"""Sensor platform for Thermostat Contact Sensors integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_ENABLED,
    CONF_TEMPERATURE_SENSORS,
    DOMAIN,
)
from .coordinator import ThermostatContactSensorsCoordinator
from .occupancy import AreaOccupancyState
from .thermostat_control import RoomTemperatureState, ThermostatState, get_temperature_from_state

from homeassistant.components.climate import HVACMode

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities."""
    coordinator: ThermostatContactSensorsCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        OpenSensorCountSensor(coordinator, entry),
        ThermostatControlSensor(coordinator, entry),
    ]

    # Create sensors for each enabled area
    for area_id, area_config in coordinator.areas_config.items():
        if area_config.get(CONF_AREA_ENABLED, True):
            # Occupancy sensor
            entities.append(
                RoomOccupancySensor(coordinator, entry, area_id)
            )
            # Temperature sensor should exist for every enabled area.
            # It will report None + diagnostics if no temp sensors are configured.
            entities.append(RoomTemperatureSensor(coordinator, entry, area_id))

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


class RoomOccupancySensor(CoordinatorEntity, SensorEntity):
    """Sensor showing occupancy status for a room/area."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-account"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
        area_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._area_id = area_id
        self._attr_unique_id = f"{entry.entry_id}_{area_id}_occupancy"

        # Get area name from registry or config
        area_config = coordinator.areas_config.get(area_id, {})
        self._area_name = area_config.get("name", area_id.replace("_", " ").title())
        self._attr_name = f"{self._area_name} Occupancy"

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    def _get_area_state(self) -> AreaOccupancyState | None:
        """Get the area state from the occupancy tracker."""
        return self.coordinator.occupancy_tracker.areas.get(self._area_id)

    @property
    def native_value(self) -> str:
        """Return the occupancy state as a string."""
        area_state = self._get_area_state()

        if area_state is None:
            return "unknown"

        if area_state.is_active:
            return "active"
        elif area_state.is_occupied:
            return "occupied"
        else:
            return "inactive"

    @property
    def icon(self) -> str:
        """Return the icon based on occupancy state."""
        value = self.native_value
        if value == "active":
            return "mdi:home-account"
        elif value == "occupied":
            return "mdi:home-clock"
        else:
            return "mdi:home-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        area_state = self._get_area_state()

        if area_state is None:
            return {
                "is_occupied": False,
                "is_active": False,
                "area_id": self._area_id,
                "area_name": self._area_name,
            }

        now = dt_util.utcnow()
        min_occupancy = self.coordinator.occupancy_tracker.min_occupancy_minutes
        grace_period = self.coordinator.occupancy_tracker.grace_period_minutes

        attrs = {
            "is_occupied": area_state.is_occupied,
            "is_active": area_state.is_active,
            "area_id": self._area_id,
            "area_name": self._area_name,
            "occupied_sensor_count": area_state.occupied_sensor_count,
            "total_sensor_count": area_state.total_sensor_count,
            "min_occupancy_minutes": min_occupancy,
            "grace_period_minutes": grace_period,
        }

        # Add occupied sensors with friendly names
        occupied_sensors = []
        for sensor_id in area_state.occupied_binary_sensors:
            state = self.hass.states.get(sensor_id)
            name = state.attributes.get("friendly_name", sensor_id) if state else sensor_id
            occupied_sensors.append({"entity_id": sensor_id, "name": name})
        for sensor_id in area_state.occupied_sensors:
            state = self.hass.states.get(sensor_id)
            name = state.attributes.get("friendly_name", sensor_id) if state else sensor_id
            occupied_sensors.append({"entity_id": sensor_id, "name": name})
        attrs["occupied_sensors"] = occupied_sensors

        # Add occupancy timing info
        if area_state.is_occupied and area_state.occupancy_start_time:
            duration_minutes = area_state.get_occupancy_minutes(now)
            attrs["occupancy_duration_minutes"] = round(duration_minutes, 1)
            attrs["occupied_since"] = area_state.occupancy_start_time.isoformat()

            if not area_state.is_active:
                # Calculate time until active
                remaining = min_occupancy - duration_minutes
                attrs["time_until_active_minutes"] = round(max(0, remaining), 1)
            else:
                attrs["time_until_active_minutes"] = 0
        else:
            attrs["occupancy_duration_minutes"] = 0
            attrs["occupied_since"] = None
            attrs["time_until_active_minutes"] = None

        # Add grace period info
        attrs["is_in_grace_period"] = area_state.is_in_grace_period
        if area_state.is_in_grace_period:
            # Calculate time until grace period expires and area becomes inactive
            unoccupancy_minutes = area_state.get_unoccupancy_minutes(now)
            remaining = grace_period - unoccupancy_minutes
            attrs["time_until_inactive_minutes"] = round(max(0, remaining), 1)
            attrs["unoccupied_since"] = area_state.unoccupancy_start_time.isoformat() if area_state.unoccupancy_start_time else None
        else:
            attrs["time_until_inactive_minutes"] = None
            attrs["unoccupied_since"] = None

        return attrs


class ThermostatControlSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing thermostat control status."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermostat"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_thermostat_control"
        self._attr_name = "Thermostat Control"

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get(CONF_NAME, "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    def _get_thermostat_state(self) -> ThermostatState | None:
        """Get the last evaluated thermostat state."""
        return self.coordinator.last_thermostat_state

    @property
    def native_value(self) -> str:
        """Return the control state as a string."""
        state = self._get_thermostat_state()

        if state is None:
            return "unknown"

        # Check for paused state first
        if self.coordinator.is_paused:
            return "paused"

        # Check HVAC mode
        if state.hvac_mode and state.hvac_mode.value == "off":
            return "off"

        # Check if no active rooms
        if state.active_room_count == 0:
            return "idle"

        # Check satiation
        if state.all_active_rooms_satiated:
            return "satiated"

        # Not satiated - determine if heating or cooling needed
        hvac_mode = state.hvac_mode.value if state.hvac_mode else "unknown"
        if hvac_mode == "heat":
            return "heating_needed"
        elif hvac_mode == "cool":
            return "cooling_needed"
        elif hvac_mode == "heat_cool":
            return "conditioning_needed"
        else:
            return "conditioning_needed"

    @property
    def icon(self) -> str:
        """Return the icon based on control state."""
        value = self.native_value
        icons = {
            "paused": "mdi:thermostat-off",
            "off": "mdi:thermostat-off",
            "idle": "mdi:thermostat",
            "satiated": "mdi:thermostat-check",
            "heating_needed": "mdi:fire",
            "cooling_needed": "mdi:snowflake",
            "conditioning_needed": "mdi:thermostat-auto",
            "unknown": "mdi:thermostat",
        }
        return icons.get(value, "mdi:thermostat")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        state = self._get_thermostat_state()
        coordinator = self.coordinator

        # Base attributes
        attrs: dict[str, Any] = {
            "thermostat_entity_id": coordinator.thermostat,
            "paused_by_contact_sensors": coordinator.is_paused,
        }

        # Get thermostat friendly name
        thermostat_state = self.hass.states.get(coordinator.thermostat)
        if thermostat_state:
            attrs["thermostat_name"] = thermostat_state.attributes.get(
                "friendly_name", coordinator.thermostat
            )

        if state is None:
            return attrs

        # HVAC mode and target temps
        attrs["hvac_mode"] = state.hvac_mode.value if state.hvac_mode else None
        attrs["is_on"] = state.is_on
        attrs["target_temperature"] = state.target_temperature
        attrs["target_temp_high"] = state.target_temp_high
        attrs["target_temp_low"] = state.target_temp_low

        # Inferred HVAC mode (calculated trend when thermostat is off)
        attrs["inferred_hvac_mode"] = state.inferred_hvac_mode.value if state.inferred_hvac_mode else None

        # Room counts
        attrs["active_room_count"] = state.active_room_count
        attrs["satiated_room_count"] = state.satiated_room_count
        attrs["all_active_rooms_satiated"] = state.all_active_rooms_satiated

        # Recommended action
        if state.recommended_action:
            attrs["recommended_action"] = state.recommended_action.value
        attrs["action_reason"] = state.action_reason

        # Cycle protection
        can_turn_on, turn_on_reason = coordinator.thermostat_controller.can_turn_on()
        can_turn_off, turn_off_reason = coordinator.thermostat_controller.can_turn_off()
        attrs["can_turn_on"] = can_turn_on
        attrs["can_turn_on_reason"] = turn_on_reason
        attrs["can_turn_off"] = can_turn_off
        attrs["can_turn_off_reason"] = turn_off_reason

        # Room-by-room status
        room_summary = {}
        for area_id, room_state in state.room_states.items():
            room_summary[area_id] = {
                "area_name": room_state.area_name,
                "is_satiated": room_state.is_satiated,
                "satiation_reason": room_state.satiation_reason.value if room_state.satiation_reason else None,
                "has_valid_readings": room_state.has_valid_readings,
                "sensor_readings": room_state.sensor_readings,
            }
            if room_state.determining_sensor:
                room_summary[area_id]["determining_sensor"] = room_state.determining_sensor
            if room_state.determining_temperature is not None:
                room_summary[area_id]["determining_temperature"] = room_state.determining_temperature
        attrs["room_summary"] = room_summary

        return attrs


class RoomTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing temperature and satiation status for a room/area."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = "°F"
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:thermometer"

    def __init__(
        self,
        coordinator: ThermostatContactSensorsCoordinator,
        entry: ConfigEntry,
        area_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._area_id = area_id
        self._attr_unique_id = f"{entry.entry_id}_{area_id}_temperature"

        # Get area name from config
        area_config = coordinator.areas_config.get(area_id, {})
        self._area_name = area_config.get("name", area_id.replace("_", " ").title())
        self._attr_name = f"{self._area_name} Temperature"

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": self._entry.data.get("name", "Thermostat Contact Sensors"),
            "manufacturer": "Custom Integration",
            "model": "Thermostat Contact Sensors",
        }

    def _get_room_state(self) -> RoomTemperatureState | None:
        """Get the room temperature state from the last thermostat evaluation."""
        thermostat_state = self.coordinator.last_thermostat_state
        if thermostat_state is None:
            return None
        return thermostat_state.room_states.get(self._area_id)

    def _get_configured_temperature_sensors(self) -> list[str]:
        area_config = self.coordinator.areas_config.get(self._area_id, {})
        sensors = area_config.get(CONF_TEMPERATURE_SENSORS, [])
        return list(sensors) if sensors else []

    def _get_live_sensor_readings(self) -> dict[str, float]:
        """Get current readings directly from HA state machine.

        This is intentionally independent of Eco/TSR/critical filtering so all rooms
        can always report temperature.
        """
        readings: dict[str, float] = {}
        if self.hass is None:
            return readings

        for sensor_id in self._get_configured_temperature_sensors():
            temp = get_temperature_from_state(self.hass.states.get(sensor_id))
            if temp is not None:
                readings[sensor_id] = temp
        return readings

    def _get_trend_mode(self) -> HVACMode | None:
        """Get the effective mode used for 'trend' decisions.

        When the thermostat is OFF, we use inferred_hvac_mode (trend).
        Otherwise, we use the current hvac_mode.
        """
        thermostat_state = self.coordinator.last_thermostat_state
        if thermostat_state is None:
            return None

        if thermostat_state.hvac_mode == HVACMode.OFF and thermostat_state.inferred_hvac_mode:
            return thermostat_state.inferred_hvac_mode
        return thermostat_state.hvac_mode

    def _compute_overall_temperature(self, readings: dict[str, float]) -> tuple[float | None, str | None]:
        """Compute the room's overall temperature based on the house trend.

        Desired behavior:
        - Trend=HEAT (house trending cold): use the coldest sensor in the room.
        - Trend=COOL (house trending hot): use the warmest sensor in the room.
        - Otherwise: fall back to average.

        Returns:
            Tuple of (temperature, sensor_id_used)
        """
        if not readings:
            return None, None

        mode = self._get_trend_mode()
        if mode == HVACMode.HEAT:
            sensor_id, temp = min(readings.items(), key=lambda x: x[1])
            return temp, sensor_id
        if mode == HVACMode.COOL:
            sensor_id, temp = max(readings.items(), key=lambda x: x[1])
            return temp, sensor_id

        avg = sum(readings.values()) / len(readings)
        # Pick the sensor closest to the avg for consistency.
        sensor_id = min(readings.keys(), key=lambda s: abs(readings[s] - avg))
        return avg, sensor_id

    @property
    def native_value(self) -> float | None:
        """Return the determining temperature for this room."""
        # Prefer the determining_temperature from the room state (which is what the
        # thermostat controller is actually using), falling back to live computation
        # only if no room state is available.
        room_state = self._get_room_state()
        if room_state is not None and room_state.determining_temperature is not None:
            return round(room_state.determining_temperature, 1)
        
        # Fallback: compute directly from sensor states
        overall_temp, _ = self._compute_overall_temperature(self._get_live_sensor_readings())
        if overall_temp is None:
            return None
        return round(overall_temp, 1)

    @property
    def icon(self) -> str:
        """Return the icon based on satiation state."""
        room_state = self._get_room_state()
        if room_state is None:
            return "mdi:thermometer"

        if room_state.is_satiated:
            return "mdi:thermometer-check"
        elif room_state.is_critical:
            return "mdi:thermometer-alert"
        else:
            return "mdi:thermometer"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        room_state = self._get_room_state()
        thermostat_state = self.coordinator.last_thermostat_state

        configured_sensors = self._get_configured_temperature_sensors()
        live_readings = self._get_live_sensor_readings()
        overall_temp, overall_sensor = self._compute_overall_temperature(live_readings)

        attrs: dict[str, Any] = {
            "area_id": self._area_id,
            "area_name": self._area_name,
            "temperature_sensors": configured_sensors,
            "live_min_temperature": min(live_readings.values()) if live_readings else None,
            "live_max_temperature": max(live_readings.values()) if live_readings else None,
            "live_avg_temperature": (sum(live_readings.values()) / len(live_readings)) if live_readings else None,
            "overall_temperature": overall_temp,
            "overall_temperature_sensor": overall_sensor,
            "trend_mode": self._get_trend_mode().value if self._get_trend_mode() else None,
        }

        if room_state is None:
            attrs["is_satiated"] = None
            attrs["satiation_reason"] = None
            attrs["has_valid_readings"] = bool(live_readings)
            attrs["sensor_readings"] = live_readings
            attrs["determining_sensor"] = overall_sensor
            attrs["determining_temperature"] = overall_temp
            return attrs

        # Satiation status
        attrs["is_satiated"] = room_state.is_satiated
        attrs["satiation_reason"] = (
            room_state.satiation_reason.value if room_state.satiation_reason else None
        )
        attrs["is_active"] = room_state.is_active
        attrs["is_critical"] = room_state.is_critical
        attrs["critical_reason"] = room_state.critical_reason

        # Temperature details
        attrs["has_valid_readings"] = room_state.has_valid_readings
        # Prefer controller readings, but include live readings for completeness.
        attrs["sensor_readings"] = room_state.sensor_readings or live_readings
        attrs["determining_sensor"] = room_state.determining_sensor
        attrs["determining_temperature"] = room_state.determining_temperature
        attrs["target_temperature"] = room_state.target_temperature

        # Distance from target
        if (
            room_state.determining_temperature is not None
            and room_state.target_temperature is not None
        ):
            attrs["distance_from_target"] = round(
                abs(room_state.determining_temperature - room_state.target_temperature),
                2,
            )
        else:
            attrs["distance_from_target"] = None

        # Sensor friendly names
        sensor_names = {}
        for sensor_id in configured_sensors:
            state = self.hass.states.get(sensor_id)
            if state:
                sensor_names[sensor_id] = state.attributes.get("friendly_name", sensor_id)
            else:
                sensor_names[sensor_id] = sensor_id
        attrs["sensor_names"] = sensor_names

        # Target temps from thermostat
        if thermostat_state:
            attrs["hvac_mode"] = (
                thermostat_state.hvac_mode.value if thermostat_state.hvac_mode else None
            )
            if thermostat_state.target_temperature is not None:
                attrs["thermostat_target"] = thermostat_state.target_temperature
            if thermostat_state.target_temp_low is not None:
                attrs["thermostat_target_low"] = thermostat_state.target_temp_low
            if thermostat_state.target_temp_high is not None:
                attrs["thermostat_target_high"] = thermostat_state.target_temp_high

        return attrs