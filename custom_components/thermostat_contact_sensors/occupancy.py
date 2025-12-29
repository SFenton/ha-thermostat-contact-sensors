"""Room occupancy tracking for Thermostat Contact Sensors integration.

This module provides occupancy detection and tracking for rooms (areas) based on
binary sensors and regular sensors. A room is considered occupied if ANY sensor
in the area indicates presence (OR logic).

Occupancy detection:
- binary_sensor: state "on" = occupied
- sensor: attribute "previous_valid_state" = "on" = occupied

A room becomes "active" (eligible for heating/cooling) when it has been
continuously occupied for a configurable number of minutes.

State persistence:
- Occupancy state is saved on shutdown and restored on startup
- This allows rooms to maintain their active status across restarts
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AREA_ENABLED,
    CONF_BINARY_SENSORS,
    CONF_SENSORS,
    DEFAULT_GRACE_PERIOD_MINUTES,
    DEFAULT_MIN_OCCUPANCY_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Storage version for state persistence
STORAGE_VERSION = 1


@dataclass
class AreaOccupancyState:
    """State tracking for a single area's occupancy."""

    area_id: str
    area_name: str
    binary_sensors: list[str] = field(default_factory=list)
    sensors: list[str] = field(default_factory=list)

    # Tracking state
    occupied_binary_sensors: set[str] = field(default_factory=set)
    occupied_sensors: set[str] = field(default_factory=set)
    occupancy_start_time: datetime | None = None
    is_active: bool = False

    # Grace period tracking - when an active room becomes unoccupied,
    # we don't immediately deactivate. We track when it became unoccupied
    # and only deactivate after the grace period expires.
    unoccupancy_start_time: datetime | None = None
    was_active_before_unoccupied: bool = False

    @property
    def is_occupied(self) -> bool:
        """Return True if ANY sensor indicates occupancy."""
        return len(self.occupied_binary_sensors) > 0 or len(self.occupied_sensors) > 0

    @property
    def all_sensors(self) -> list[str]:
        """Return all sensors being tracked for this area."""
        return self.binary_sensors + self.sensors

    @property
    def occupied_sensor_count(self) -> int:
        """Return the number of sensors currently indicating occupancy."""
        return len(self.occupied_binary_sensors) + len(self.occupied_sensors)

    @property
    def total_sensor_count(self) -> int:
        """Return the total number of occupancy sensors in this area."""
        return len(self.binary_sensors) + len(self.sensors)

    def get_occupancy_duration(self, now: datetime | None = None) -> timedelta | None:
        """Return how long the room has been continuously occupied.

        Returns None if the room is not currently occupied.
        """
        if not self.is_occupied or self.occupancy_start_time is None:
            return None

        if now is None:
            now = dt_util.utcnow()

        return now - self.occupancy_start_time

    def get_occupancy_minutes(self, now: datetime | None = None) -> float:
        """Return occupancy duration in minutes, or 0 if not occupied."""
        duration = self.get_occupancy_duration(now)
        if duration is None:
            return 0.0
        return duration.total_seconds() / 60.0

    def get_unoccupancy_duration(self, now: datetime | None = None) -> timedelta | None:
        """Return how long the room has been continuously unoccupied.

        Returns None if the room is currently occupied or never became unoccupied.
        """
        if self.is_occupied or self.unoccupancy_start_time is None:
            return None

        if now is None:
            now = dt_util.utcnow()

        return now - self.unoccupancy_start_time

    def get_unoccupancy_minutes(self, now: datetime | None = None) -> float:
        """Return unoccupancy duration in minutes, or 0 if occupied."""
        duration = self.get_unoccupancy_duration(now)
        if duration is None:
            return 0.0
        return duration.total_seconds() / 60.0

    @property
    def is_in_grace_period(self) -> bool:
        """Return True if this area is in the grace period after becoming unoccupied.

        An area is in grace period when:
        - It's currently unoccupied
        - It was active before becoming unoccupied
        - The unoccupancy tracking has started
        """
        return (
            not self.is_occupied
            and self.was_active_before_unoccupied
            and self.unoccupancy_start_time is not None
        )

    def to_storage_dict(self) -> dict[str, Any]:
        """Serialize the occupancy state for storage.

        Only persists state that needs to survive restarts.
        """
        return {
            "area_id": self.area_id,
            "is_active": self.is_active,
            "occupancy_start_time": self.occupancy_start_time.isoformat() if self.occupancy_start_time else None,
            "was_active_before_unoccupied": self.was_active_before_unoccupied,
            "unoccupancy_start_time": self.unoccupancy_start_time.isoformat() if self.unoccupancy_start_time else None,
        }

    def restore_from_storage(self, data: dict[str, Any]) -> None:
        """Restore occupancy state from storage.

        Args:
            data: Stored state dictionary.
        """
        if data.get("is_active"):
            self.is_active = True

        if data.get("occupancy_start_time"):
            try:
                self.occupancy_start_time = datetime.fromisoformat(data["occupancy_start_time"])
            except (ValueError, TypeError):
                pass

        if data.get("was_active_before_unoccupied"):
            self.was_active_before_unoccupied = True

        if data.get("unoccupancy_start_time"):
            try:
                self.unoccupancy_start_time = datetime.fromisoformat(data["unoccupancy_start_time"])
            except (ValueError, TypeError):
                pass


def is_binary_sensor_occupied(state: State | None) -> bool:
    """Check if a binary_sensor indicates occupancy.

    Args:
        state: The state object for the binary sensor.

    Returns:
        True if state is "on", False otherwise.
    """
    if state is None:
        return False

    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False

    return state.state == STATE_ON


def is_sensor_occupied(state: State | None) -> bool:
    """Check if a sensor indicates occupancy via previous_valid_state attribute.

    Args:
        state: The state object for the sensor.

    Returns:
        True if the "previous_valid_state" attribute is "on", False otherwise.
    """
    if state is None:
        return False

    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False

    # Check the previous_valid_state attribute
    previous_valid_state = state.attributes.get("previous_valid_state")
    return previous_valid_state == STATE_ON


def get_sensor_occupancy_state(entity_id: str, state: State | None) -> bool:
    """Determine occupancy state for any sensor type.

    Args:
        entity_id: The entity ID of the sensor.
        state: The state object for the sensor.

    Returns:
        True if the sensor indicates occupancy, False otherwise.
    """
    if state is None:
        return False

    # Determine sensor type from domain
    domain = entity_id.split(".")[0] if "." in entity_id else ""

    if domain == "binary_sensor":
        return is_binary_sensor_occupied(state)
    elif domain == "sensor":
        return is_sensor_occupied(state)
    else:
        # Unknown sensor type, default to checking for "on" state
        _LOGGER.warning("Unknown sensor domain for occupancy: %s", entity_id)
        return state.state == STATE_ON


class RoomOccupancyTracker:
    """Track room occupancy across multiple areas.

    This class monitors occupancy sensors in configured areas and tracks:
    - Which sensors currently indicate occupancy
    - When continuous occupancy started for each area
    - Whether an area is "active" (occupied long enough to be considered for heating/cooling)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        areas_config: dict[str, dict[str, Any]],
        min_occupancy_minutes: int = DEFAULT_MIN_OCCUPANCY_MINUTES,
        grace_period_minutes: int = DEFAULT_GRACE_PERIOD_MINUTES,
        entry_id: str | None = None,
    ) -> None:
        """Initialize the room occupancy tracker.

        Args:
            hass: The Home Assistant instance.
            areas_config: Configuration dict for areas (from config entry data).
            min_occupancy_minutes: Minutes of continuous occupancy required
                                   for a room to be considered "active".
            grace_period_minutes: Minutes to wait before deactivating when an
                                  active room becomes unoccupied. Minimum 2.
            entry_id: Config entry ID for state persistence storage.
        """
        self.hass = hass
        self._min_occupancy_minutes = min_occupancy_minutes
        self._grace_period_minutes = max(2, grace_period_minutes)
        self._areas: dict[str, AreaOccupancyState] = {}
        self._unsub_state_change: callable | None = None
        self._unsub_time_interval: callable | None = None
        self._update_callbacks: list[callable] = []
        self._entry_id = entry_id

        # Set up storage for state persistence
        if entry_id:
            self._store: Store | None = Store(
                hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.occupancy"
            )
        else:
            self._store = None

        # Build area tracking from config
        self._build_area_tracking(areas_config)

    @property
    def min_occupancy_minutes(self) -> int:
        """Return the minimum occupancy minutes threshold."""
        return self._min_occupancy_minutes

    @min_occupancy_minutes.setter
    def min_occupancy_minutes(self, value: int) -> None:
        """Set the minimum occupancy minutes threshold."""
        self._min_occupancy_minutes = value
        # Re-evaluate active status for all areas
        self._update_all_active_status()

    @property
    def grace_period_minutes(self) -> int:
        """Return the grace period minutes threshold."""
        return self._grace_period_minutes

    @grace_period_minutes.setter
    def grace_period_minutes(self, value: int) -> None:
        """Set the grace period minutes threshold (minimum 2)."""
        self._grace_period_minutes = max(2, value)
        # Re-evaluate active status for all areas
        self._update_all_active_status()

    @property
    def areas(self) -> dict[str, AreaOccupancyState]:
        """Return all tracked areas."""
        return self._areas

    @property
    def all_tracked_sensors(self) -> list[str]:
        """Return all sensors being tracked across all areas."""
        sensors = []
        for area in self._areas.values():
            sensors.extend(area.all_sensors)
        return sensors

    @property
    def occupied_areas(self) -> list[AreaOccupancyState]:
        """Return list of currently occupied areas."""
        return [area for area in self._areas.values() if area.is_occupied]

    @property
    def active_areas(self) -> list[AreaOccupancyState]:
        """Return list of active areas (occupied long enough).

        An area is "active" when it has been continuously occupied for
        at least min_occupancy_minutes.
        """
        return [area for area in self._areas.values() if area.is_active]

    @property
    def inactive_areas(self) -> list[AreaOccupancyState]:
        """Return list of inactive areas (not active).

        These are areas that are either unoccupied or haven't been
        occupied long enough to become active.
        """
        return [area for area in self._areas.values() if not area.is_active]

    @property
    def any_area_occupied(self) -> bool:
        """Return True if any area is currently occupied."""
        return len(self.occupied_areas) > 0

    @property
    def any_area_active(self) -> bool:
        """Return True if any area is active (occupied long enough)."""
        return len(self.active_areas) > 0

    def _build_area_tracking(self, areas_config: dict[str, dict[str, Any]]) -> None:
        """Build area tracking structures from configuration.

        Args:
            areas_config: Configuration dict for areas.
        """
        self._areas = {}

        for area_id, area_config in areas_config.items():
            # Skip disabled areas
            if not area_config.get(CONF_AREA_ENABLED, True):
                continue

            # Get occupancy sensors for this area
            binary_sensors = area_config.get(CONF_BINARY_SENSORS, [])
            sensors = area_config.get(CONF_SENSORS, [])

            # Only track areas that have occupancy sensors configured
            if not binary_sensors and not sensors:
                continue

            self._areas[area_id] = AreaOccupancyState(
                area_id=area_id,
                area_name=area_config.get("name", area_id),
                binary_sensors=list(binary_sensors),
                sensors=list(sensors),
            )

        _LOGGER.debug(
            "Built occupancy tracking for %d areas with %d total sensors",
            len(self._areas),
            len(self.all_tracked_sensors),
        )

    def update_config(self, areas_config: dict[str, dict[str, Any]]) -> None:
        """Update area configuration.

        This will rebuild the tracking structures and re-scan sensor states.

        Args:
            areas_config: New configuration dict for areas.
        """
        self._build_area_tracking(areas_config)
        self._scan_all_sensors()

    async def async_setup(self) -> None:
        """Set up the occupancy tracker and start listening to state changes."""
        # Restore state from storage before scanning
        await self._async_restore_state()

        # Initial scan of all sensor states
        self._scan_all_sensors()

        # Subscribe to state changes for all tracked sensors
        all_sensors = self.all_tracked_sensors
        if all_sensors:
            self._unsub_state_change = async_track_state_change_event(
                self.hass,
                all_sensors,
                self._async_sensor_state_changed,
            )

        # Set up periodic timer to update active status as occupancy duration increases
        # This ensures areas transition from occupied to active after min_occupancy_minutes
        self._unsub_time_interval = async_track_time_interval(
            self.hass,
            self._async_periodic_update,
            timedelta(seconds=30),
        )

        _LOGGER.debug(
            "Occupancy tracker setup complete. Monitoring %d sensors across %d areas",
            len(all_sensors),
            len(self._areas),
        )

    @callback
    def _async_periodic_update(self, now: datetime) -> None:
        """Periodically update active status for all areas.

        This is called every 30 seconds to check if any occupied areas
        have been occupied long enough to become active.
        """
        self.force_update_active_status()

    async def async_shutdown(self) -> None:
        """Shut down the occupancy tracker."""
        # Save state before shutting down
        await self._async_save_state()

        if self._unsub_state_change:
            self._unsub_state_change()
            self._unsub_state_change = None

        if self._unsub_time_interval:
            self._unsub_time_interval()
            self._unsub_time_interval = None

        _LOGGER.debug("Occupancy tracker shut down")

    async def _async_save_state(self) -> None:
        """Save current occupancy state to storage."""
        if self._store is None:
            return

        state_data = {
            "version": STORAGE_VERSION,
            "saved_at": dt_util.utcnow().isoformat(),
            "areas": {
                area_id: area.to_storage_dict()
                for area_id, area in self._areas.items()
            },
        }

        await self._store.async_save(state_data)
        _LOGGER.debug("Saved occupancy state for %d areas", len(self._areas))

    async def _async_restore_state(self) -> None:
        """Restore occupancy state from storage."""
        if self._store is None:
            return

        stored_data = await self._store.async_load()
        if stored_data is None:
            _LOGGER.debug("No stored occupancy state found")
            return

        areas_data = stored_data.get("areas", {})
        restored_count = 0

        for area_id, area_state_data in areas_data.items():
            if area_id in self._areas:
                self._areas[area_id].restore_from_storage(area_state_data)
                restored_count += 1
                _LOGGER.debug(
                    "Restored state for area %s: is_active=%s",
                    area_id,
                    self._areas[area_id].is_active,
                )

        _LOGGER.info(
            "Restored occupancy state for %d areas (saved at %s)",
            restored_count,
            stored_data.get("saved_at", "unknown"),
        )

    def register_update_callback(self, callback: callable) -> callable:
        """Register a callback to be called when occupancy state changes.

        Args:
            callback: A callable that takes no arguments.

        Returns:
            A callable to unregister the callback.
        """
        self._update_callbacks.append(callback)

        def unregister():
            if callback in self._update_callbacks:
                self._update_callbacks.remove(callback)

        return unregister

    def _notify_update(self) -> None:
        """Notify all registered callbacks of an update."""
        for cb in self._update_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in occupancy update callback")

    def _scan_all_sensors(self) -> None:
        """Scan all sensors and update occupancy state."""
        now = dt_util.utcnow()

        for area in self._areas.values():
            self._update_area_occupancy(area, now)

    def _update_area_occupancy(self, area: AreaOccupancyState, now: datetime) -> None:
        """Update occupancy state for a single area.

        Args:
            area: The area state to update.
            now: Current timestamp.
        """
        was_occupied = area.is_occupied
        had_restored_occupancy_start = area.occupancy_start_time is not None

        # Check all binary sensors
        area.occupied_binary_sensors = set()
        for sensor in area.binary_sensors:
            state = self.hass.states.get(sensor)
            if is_binary_sensor_occupied(state):
                area.occupied_binary_sensors.add(sensor)

        # Check all regular sensors
        area.occupied_sensors = set()
        for sensor in area.sensors:
            state = self.hass.states.get(sensor)
            if is_sensor_occupied(state):
                area.occupied_sensors.add(sensor)

        is_now_occupied = area.is_occupied

        # Handle occupancy state transitions
        if is_now_occupied and not was_occupied:
            # Just became occupied
            # If returning during grace period, maintain activity by back-dating
            # the occupancy start time to ensure we exceed the threshold
            if area.was_active_before_unoccupied:
                # Room was active before brief unoccupancy - back-date to maintain active status
                area.occupancy_start_time = now - timedelta(
                    minutes=self._min_occupancy_minutes + 1
                )
                _LOGGER.debug(
                    "Area %s became occupied during grace period at %s - remaining active",
                    area.area_id,
                    now.isoformat(),
                )
            elif had_restored_occupancy_start:
                # We have a restored occupancy_start_time from storage, keep it
                _LOGGER.debug(
                    "Area %s using restored occupancy start time %s",
                    area.area_id,
                    area.occupancy_start_time.isoformat() if area.occupancy_start_time else "None",
                )
            else:
                # Normal occupancy - start timing fresh
                area.occupancy_start_time = now
                _LOGGER.debug(
                    "Area %s became occupied at %s",
                    area.area_id,
                    now.isoformat(),
                )
            # Clear grace period tracking
            area.unoccupancy_start_time = None
            area.was_active_before_unoccupied = False
        elif not is_now_occupied and was_occupied:
            # Just became unoccupied - start grace period if was active
            area.occupancy_start_time = None
            if area.is_active:
                # Start grace period - don't deactivate yet
                area.unoccupancy_start_time = now
                area.was_active_before_unoccupied = True
                _LOGGER.debug(
                    "Area %s became unoccupied while active - starting grace period",
                    area.area_id,
                )
            else:
                # Wasn't active, just reset
                area.unoccupancy_start_time = None
                area.was_active_before_unoccupied = False
                _LOGGER.debug("Area %s became unoccupied", area.area_id)

        # Update active status
        self._update_area_active_status(area, now)

    def _update_area_active_status(
        self, area: AreaOccupancyState, now: datetime
    ) -> None:
        """Update the active status for an area based on occupancy duration.

        Args:
            area: The area state to update.
            now: Current timestamp.
        """
        was_active = area.is_active

        if area.is_occupied:
            # Room is occupied - check if it's been long enough to become active
            occupancy_minutes = area.get_occupancy_minutes(now)
            area.is_active = occupancy_minutes >= self._min_occupancy_minutes
        elif area.is_in_grace_period:
            # Room is unoccupied but in grace period - check if grace period expired
            unoccupancy_minutes = area.get_unoccupancy_minutes(now)
            if unoccupancy_minutes >= self._grace_period_minutes:
                # Grace period expired - deactivate
                area.is_active = False
                area.was_active_before_unoccupied = False
                area.unoccupancy_start_time = None
                _LOGGER.debug(
                    "Area %s grace period expired (unoccupied for %.1f minutes) - deactivating",
                    area.area_id,
                    unoccupancy_minutes,
                )
            else:
                # Still in grace period - remain active
                area.is_active = True
        else:
            # Room is unoccupied and not in grace period
            area.is_active = False

        if area.is_active and not was_active:
            _LOGGER.debug(
                "Area %s became active (occupied for %.1f minutes)",
                area.area_id,
                area.get_occupancy_minutes(now),
            )
        elif not area.is_active and was_active and not area.is_in_grace_period:
            _LOGGER.debug("Area %s became inactive", area.area_id)

    def _update_all_active_status(self) -> None:
        """Update active status for all areas."""
        now = dt_util.utcnow()
        for area in self._areas.values():
            self._update_area_active_status(area, now)

    @callback
    def _async_sensor_state_changed(self, event) -> None:
        """Handle sensor state changes."""
        entity_id = event.data.get("entity_id")
        new_state: State | None = event.data.get("new_state")
        old_state: State | None = event.data.get("old_state")

        if new_state is None:
            return

        # Find which area this sensor belongs to
        area = self._find_area_for_sensor(entity_id)
        if area is None:
            return

        now = dt_util.utcnow()

        # Determine if occupancy state changed for this sensor
        was_occupied = self._was_sensor_occupied(entity_id, old_state)
        is_occupied = get_sensor_occupancy_state(entity_id, new_state)

        if was_occupied != is_occupied:
            _LOGGER.debug(
                "Sensor %s occupancy changed: %s -> %s",
                entity_id,
                was_occupied,
                is_occupied,
            )

            # Update the area's occupancy state
            self._update_area_occupancy(area, now)

            # Notify listeners
            self._notify_update()

    def _find_area_for_sensor(self, entity_id: str) -> AreaOccupancyState | None:
        """Find the area that contains a given sensor.

        Args:
            entity_id: The entity ID to look up.

        Returns:
            The AreaOccupancyState if found, None otherwise.
        """
        for area in self._areas.values():
            if entity_id in area.all_sensors:
                return area
        return None

    def _was_sensor_occupied(self, entity_id: str, old_state: State | None) -> bool:
        """Determine if a sensor was previously indicating occupancy.

        Args:
            entity_id: The entity ID.
            old_state: The previous state object.

        Returns:
            True if the sensor was indicating occupancy.
        """
        if old_state is None:
            return False

        return get_sensor_occupancy_state(entity_id, old_state)

    def get_area(self, area_id: str) -> AreaOccupancyState | None:
        """Get the occupancy state for a specific area.

        Args:
            area_id: The area ID to look up.

        Returns:
            The AreaOccupancyState if found, None otherwise.
        """
        return self._areas.get(area_id)

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of current occupancy state.

        Returns:
            A dict with occupancy summary information.
        """
        return {
            "total_areas": len(self._areas),
            "occupied_areas": len(self.occupied_areas),
            "active_areas": len(self.active_areas),
            "min_occupancy_minutes": self._min_occupancy_minutes,
            "grace_period_minutes": self._grace_period_minutes,
            "areas": {
                area_id: {
                    "name": area.area_name,
                    "is_occupied": area.is_occupied,
                    "is_active": area.is_active,
                    "occupancy_minutes": area.get_occupancy_minutes(),
                    "occupied_sensors": list(area.occupied_binary_sensors)
                    + list(area.occupied_sensors),
                    "total_sensors": area.total_sensor_count,
                }
                for area_id, area in self._areas.items()
            },
        }

    def force_update_active_status(self) -> None:
        """Force an update of all areas' active status.

        This should be called periodically to update the active status
        for areas that are continuously occupied and to refresh sensor
        attributes like occupancy_duration_minutes.
        """
        now = dt_util.utcnow()

        for area in self._areas.values():
            self._update_area_active_status(area, now)

        # Always notify to refresh sensor attributes (duration, time remaining, etc.)
        self._notify_update()
