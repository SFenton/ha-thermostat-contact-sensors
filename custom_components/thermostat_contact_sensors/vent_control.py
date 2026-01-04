"""Vent control logic for Thermostat Contact Sensors integration.

This module manages HVAC vents (cover entities with tilt support) to control
airflow to individual rooms based on occupancy and temperature conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import HVACMode
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OPEN,
    STATE_CLOSED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .occupancy import AreaOccupancyState
    from .thermostat_control import RoomTemperatureState

_LOGGER = logging.getLogger(__name__)

# Service names for tilt control
SERVICE_OPEN_COVER_TILT = "open_cover_tilt"
SERVICE_CLOSE_COVER_TILT = "close_cover_tilt"


@dataclass
class VentState:
    """State of a single vent or vent group."""

    entity_id: str
    area_id: str
    is_group: bool = False
    member_count: int = 1  # Number of vents (1 for single, N for groups)
    is_open: bool = False
    should_be_open: bool = False
    last_command_time: datetime | None = None
    open_reason: str | None = None


@dataclass
class AreaVentState:
    """Vent state for an area."""

    area_id: str
    area_name: str
    vents: list[VentState] = field(default_factory=list)
    total_vent_count: int = 0  # Sum of all member_count values
    open_vent_count: int = 0  # Sum of open vent member counts
    should_open: bool = False
    open_reason: str | None = None
    occupancy_start_time: datetime | None = None
    distance_from_target: float | None = None  # How far from target temp
    determining_temperature: float | None = None  # Actual temperature for priority sorting


@dataclass
class VentControlState:
    """Overall vent control state."""

    total_vents: int = 0
    open_vents: int = 0
    vents_should_be_open: int = 0
    area_states: dict[str, AreaVentState] = field(default_factory=dict)
    pending_commands: list[tuple[str, bool, str]] = field(
        default_factory=list
    )  # (entity_id, should_open, reason)


class VentController:
    """Controller for managing HVAC vents based on room state."""

    def __init__(
        self,
        hass: HomeAssistant,
        min_vents_open: int = 5,
        vent_open_delay_seconds: int = 30,
        vent_debounce_seconds: int = 30,
    ) -> None:
        """Initialize the vent controller.

        Args:
            hass: Home Assistant instance.
            min_vents_open: Minimum number of vents that must remain open.
            vent_open_delay_seconds: Seconds after occupancy before vents open.
            vent_debounce_seconds: Minimum time between vent state changes.
        """
        self.hass = hass
        self._min_vents_open = min_vents_open
        self._vent_open_delay_seconds = vent_open_delay_seconds
        self._vent_debounce_seconds = vent_debounce_seconds

        # Track last command time per vent for debouncing
        self._last_command_times: dict[str, datetime] = {}

        # Track vent states
        self._vent_states: dict[str, VentState] = {}

    @property
    def min_vents_open(self) -> int:
        """Return minimum vents that must remain open."""
        return self._min_vents_open

    @min_vents_open.setter
    def min_vents_open(self, value: int) -> None:
        """Set minimum vents that must remain open."""
        self._min_vents_open = value

    @property
    def vent_open_delay_seconds(self) -> int:
        """Return vent open delay in seconds."""
        return self._vent_open_delay_seconds

    @vent_open_delay_seconds.setter
    def vent_open_delay_seconds(self, value: int) -> None:
        """Set vent open delay in seconds."""
        self._vent_open_delay_seconds = value

    @property
    def vent_debounce_seconds(self) -> int:
        """Return vent debounce time in seconds."""
        return self._vent_debounce_seconds

    @vent_debounce_seconds.setter
    def vent_debounce_seconds(self, value: int) -> None:
        """Set vent debounce time in seconds."""
        self._vent_debounce_seconds = value

    def get_group_member_count(self, entity_id: str) -> int:
        """Get the number of members in a cover group.

        Args:
            entity_id: The entity ID to check.

        Returns:
            Number of members if it's a group, 1 otherwise.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            return 1

        # Check if this is a cover group by looking for entity_id attribute
        members = state.attributes.get(ATTR_ENTITY_ID)
        if members and isinstance(members, (list, tuple)):
            return len(members)

        return 1

    def is_cover_group(self, entity_id: str) -> bool:
        """Check if an entity is a cover group.

        Args:
            entity_id: The entity ID to check.

        Returns:
            True if the entity is a cover group.
        """
        state = self.hass.states.get(entity_id)
        if state is None:
            return False

        # Check if this has entity_id attribute (indicates a group)
        members = state.attributes.get(ATTR_ENTITY_ID)
        return members is not None and isinstance(members, (list, tuple))

    def get_vent_current_state(self, entity_id: str) -> bool:
        """Get the current open/closed state of a vent.

        Args:
            entity_id: The vent entity ID.

        Returns:
            True if the vent is open, False otherwise.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False

        # Consider open if state is "open" or if tilt position > 50%
        if state.state == STATE_OPEN:
            return True

        # Check tilt position
        tilt_position = state.attributes.get("current_tilt_position")
        if tilt_position is not None:
            return tilt_position > 50

        return state.state != STATE_CLOSED

    def can_send_command(self, entity_id: str, now: datetime | None = None) -> tuple[bool, str]:
        """Check if a command can be sent to a vent (debounce check).

        Args:
            entity_id: The vent entity ID.
            now: Current time (optional, for testing).

        Returns:
            Tuple of (can_send, reason).
        """
        if now is None:
            now = dt_util.utcnow()

        last_command = self._last_command_times.get(entity_id)
        if last_command is None:
            return True, "No previous command"

        elapsed = (now - last_command).total_seconds()
        if elapsed < self._vent_debounce_seconds:
            remaining = self._vent_debounce_seconds - elapsed
            return False, f"Debounce: {remaining:.0f}s remaining"

        return True, "Debounce period passed"

    def evaluate_area_vents(
        self,
        area_id: str,
        area_name: str,
        vents: list[str],
        is_active: bool,
        is_occupied: bool,
        is_satiated: bool,
        is_critical: bool,
        occupancy_start_time: datetime | None,
        distance_from_target: float | None,
        determining_temperature: float | None = None,
        area_vent_open_delay: int | None = None,
        now: datetime | None = None,
    ) -> AreaVentState:
        """Evaluate vent states for an area.

        Args:
            area_id: The area ID.
            area_name: The area name.
            vents: List of vent entity IDs for this area.
            is_active: Whether the room is active (occupied long enough).
            is_occupied: Whether the room is currently occupied.
            is_satiated: Whether the room is at target temperature.
            is_critical: Whether the room is critically cold/hot.
            occupancy_start_time: When the room became occupied.
            distance_from_target: How far from target temperature (for prioritization).
            determining_temperature: Actual temperature for HVAC-aware priority sorting.
            area_vent_open_delay: Per-area override for vent open delay.
            now: Current time (optional, for testing).

        Returns:
            AreaVentState with evaluated vent states.
        """
        if now is None:
            now = dt_util.utcnow()

        open_delay = (
            area_vent_open_delay
            if area_vent_open_delay is not None
            else self._vent_open_delay_seconds
        )

        area_state = AreaVentState(
            area_id=area_id,
            area_name=area_name,
            occupancy_start_time=occupancy_start_time,
            distance_from_target=distance_from_target,
            determining_temperature=determining_temperature,
        )

        # Determine if vents should be open for this area
        # Priority order:
        # 1. Critical - always open (safety)
        # 2. Occupied past delay - open (comfort for people in room)
        # 3. Active and unsatiated - open (needs conditioning)
        # 4. Satiated or inactive - closed
        should_open = False
        open_reason = None

        if is_critical:
            # Critical rooms always have vents open
            should_open = True
            open_reason = "Critical temperature"
        elif is_occupied and occupancy_start_time is not None:
            # Check if occupied long enough for vents to open
            occupied_seconds = (now - occupancy_start_time).total_seconds()
            if occupied_seconds >= open_delay:
                should_open = True
                open_reason = f"Occupied for {occupied_seconds:.0f}s (>= {open_delay}s)"
            elif is_active and not is_satiated:
                # Not occupied long enough, but active and needs conditioning
                should_open = True
                open_reason = "Active, needs conditioning"
            else:
                should_open = False
                open_reason = f"Occupied only {occupied_seconds:.0f}s (< {open_delay}s delay)"
        elif is_active and not is_satiated:
            # Active unsatiated rooms need vents open
            should_open = True
            open_reason = "Active, needs conditioning"
        elif is_satiated:
            # Satiated rooms close vents (temperature reached)
            should_open = False
            open_reason = "Satiated - at target temperature"
        else:
            # Inactive - close vents
            should_open = False
            open_reason = "Inactive"

        area_state.should_open = should_open
        area_state.open_reason = open_reason

        # Evaluate each vent
        for vent_entity_id in vents:
            is_group = self.is_cover_group(vent_entity_id)
            member_count = self.get_group_member_count(vent_entity_id)
            is_open = self.get_vent_current_state(vent_entity_id)

            vent_state = VentState(
                entity_id=vent_entity_id,
                area_id=area_id,
                is_group=is_group,
                member_count=member_count,
                is_open=is_open,
                should_be_open=should_open,
                last_command_time=self._last_command_times.get(vent_entity_id),
                open_reason=open_reason if should_open else None,
            )

            area_state.vents.append(vent_state)
            area_state.total_vent_count += member_count
            if is_open:
                area_state.open_vent_count += member_count

        return area_state

    def calculate_minimum_vents_priority(
        self,
        area_states: dict[str, AreaVentState],
        hvac_mode: HVACMode | None = None,
    ) -> list[tuple[str, str, int, float]]:
        """Calculate priority order for keeping minimum vents open.

        When we need to keep vents open for back pressure prevention, we prioritize:
        1. Critical rooms (temperature emergency)
        2. Active rooms (people actively there)
        3. Temperature-based: coldest rooms for HEAT, hottest for COOL
        4. Occupied rooms (presence but no activity)
        - Fallback: absolute distance from target

        Args:
            area_states: Dict of area_id -> AreaVentState.
            hvac_mode: Current HVAC mode for temperature-aware sorting.

        Returns:
            List of (area_id, vent_entity_id, member_count, priority_score).
            Higher score = higher priority for staying open.
        """
        priority_list: list[tuple[str, str, int, float]] = []

        for area_id, area_state in area_states.items():
            for vent in area_state.vents:
                priority_score = 0.0

                # Critical rooms get highest priority
                if area_state.should_open and "Critical" in (area_state.open_reason or ""):
                    priority_score += 2000.0

                # Active rooms get second priority
                elif area_state.should_open and "Active" in (area_state.open_reason or ""):
                    priority_score += 1000.0

                # Occupied rooms get low priority (temperature-based beats this)
                elif area_state.should_open and "Occupied" in (area_state.open_reason or ""):
                    priority_score += 50.0

                # Temperature-based priority for non-active/occupied/critical rooms
                # For minimum vent selection, we want rooms that actually need conditioning
                if area_state.determining_temperature is not None:
                    if hvac_mode == HVACMode.HEAT:
                        # For heating: coldest rooms get priority (lower temp = higher priority)
                        # Invert so that colder rooms score higher
                        # e.g., 60°F -> +200, 70°F -> +100, 80°F -> +0
                        priority_score += max(0, (80.0 - area_state.determining_temperature) * 10.0)
                    elif hvac_mode == HVACMode.COOL:
                        # For cooling: hottest rooms get priority (higher temp = higher priority)
                        # e.g., 80°F -> +200, 70°F -> +100, 60°F -> +0
                        priority_score += max(0, (area_state.determining_temperature - 60.0) * 10.0)
                    else:
                        # Fallback for heat_cool or unknown: use absolute distance
                        if area_state.distance_from_target is not None:
                            priority_score += area_state.distance_from_target * 10.0
                elif area_state.distance_from_target is not None:
                    # No temperature reading, fall back to distance
                    priority_score += area_state.distance_from_target * 10.0

                priority_list.append(
                    (area_id, vent.entity_id, vent.member_count, priority_score)
                )

        # Sort by priority score descending
        priority_list.sort(key=lambda x: x[3], reverse=True)
        return priority_list

    def evaluate_all_vents(
        self,
        area_vent_configs: dict[str, list[str]],
        active_areas: list["AreaOccupancyState"],
        occupied_areas: list["AreaOccupancyState"],
        room_temp_states: dict[str, "RoomTemperatureState"] | None = None,
        area_vent_delays: dict[str, int] | None = None,
        hvac_mode: HVACMode | None = None,
        now: datetime | None = None,
    ) -> VentControlState:
        """Evaluate all vents and determine which should be open.

        Args:
            area_vent_configs: Dict of area_id -> list of vent entity IDs.
            active_areas: List of active AreaOccupancyState objects.
            occupied_areas: List of occupied AreaOccupancyState objects.
            room_temp_states: Dict of area_id -> RoomTemperatureState.
            area_vent_delays: Dict of area_id -> per-area vent open delay override.
            hvac_mode: Current HVAC mode for temperature-aware vent priority.
            now: Current time (optional, for testing).

        Returns:
            VentControlState with all vent evaluations.
        """
        if now is None:
            now = dt_util.utcnow()

        if room_temp_states is None:
            room_temp_states = {}

        if area_vent_delays is None:
            area_vent_delays = {}

        control_state = VentControlState()

        # Build lookup sets
        active_area_ids = {a.area_id for a in active_areas}
        occupied_area_ids = {a.area_id for a in occupied_areas}

        # Build occupancy start time lookup
        occupancy_times: dict[str, datetime | None] = {}
        for area in occupied_areas:
            occupancy_times[area.area_id] = area.occupancy_start_time
        for area in active_areas:
            if area.area_id not in occupancy_times:
                occupancy_times[area.area_id] = area.occupancy_start_time

        # Evaluate each area
        for area_id, vents in area_vent_configs.items():
            if not vents:
                continue

            is_active = area_id in active_area_ids
            is_occupied = area_id in occupied_area_ids

            # Get temperature state for this area
            temp_state = room_temp_states.get(area_id)
            is_satiated = temp_state.is_satiated if temp_state else False
            is_critical = temp_state.is_critical if temp_state else False
            distance_from_target = None
            determining_temperature = None

            if temp_state and temp_state.determining_temperature is not None:
                determining_temperature = temp_state.determining_temperature
                # Calculate distance from target for prioritization
                if temp_state.is_satiated:
                    distance_from_target = 0.0
                elif temp_state.target_temperature is not None:
                    distance_from_target = abs(
                        temp_state.determining_temperature
                        - temp_state.target_temperature
                    )
                else:
                    distance_from_target = 0.0

            # Get area name from first occupied/active area match
            area_name = area_id
            for area in active_areas + occupied_areas:
                if area.area_id == area_id:
                    area_name = area.area_name
                    break

            area_state = self.evaluate_area_vents(
                area_id=area_id,
                area_name=area_name,
                vents=vents,
                is_active=is_active,
                is_occupied=is_occupied,
                is_satiated=is_satiated,
                is_critical=is_critical,
                occupancy_start_time=occupancy_times.get(area_id),
                distance_from_target=distance_from_target,
                determining_temperature=determining_temperature,
                area_vent_open_delay=area_vent_delays.get(area_id),
                now=now,
            )

            control_state.area_states[area_id] = area_state
            control_state.total_vents += area_state.total_vent_count
            control_state.open_vents += area_state.open_vent_count

        # Now apply minimum vents open logic
        # First count how many vents should be open based on rules
        vents_should_open = 0
        vents_to_close: list[VentState] = []

        for area_state in control_state.area_states.values():
            for vent in area_state.vents:
                if vent.should_be_open:
                    vents_should_open += vent.member_count
                else:
                    vents_to_close.append(vent)

        control_state.vents_should_be_open = vents_should_open

        # If we need to keep more vents open for back pressure prevention
        if vents_should_open < self._min_vents_open:
            needed = self._min_vents_open - vents_should_open

            # Get priority list for vents that would otherwise close
            priority_list = self.calculate_minimum_vents_priority(
                control_state.area_states,
                hvac_mode=hvac_mode,
            )

            # Keep vents open in priority order until we hit minimum
            for area_id, vent_entity_id, member_count, _ in priority_list:
                if needed <= 0:
                    break

                # Find this vent and mark it to stay open
                area_state = control_state.area_states.get(area_id)
                if area_state:
                    for vent in area_state.vents:
                        if vent.entity_id == vent_entity_id and not vent.should_be_open:
                            vent.should_be_open = True
                            vent.open_reason = (
                                f"Minimum vents (need {self._min_vents_open})"
                            )
                            needed -= member_count
                            control_state.vents_should_be_open += member_count
                            break

        # Generate pending commands
        for area_state in control_state.area_states.values():
            for vent in area_state.vents:
                if vent.should_be_open != vent.is_open:
                    can_send, reason = self.can_send_command(vent.entity_id, now)
                    if can_send:
                        control_state.pending_commands.append(
                            (
                                vent.entity_id,
                                vent.should_be_open,
                                vent.open_reason or "Close vent",
                            )
                        )
                    else:
                        _LOGGER.debug(
                            "Skipping command for %s: %s",
                            vent.entity_id,
                            reason,
                        )

        return control_state

    async def async_execute_vent_commands(
        self,
        control_state: VentControlState,
        now: datetime | None = None,
    ) -> int:
        """Execute pending vent commands.

        Args:
            control_state: The VentControlState with pending commands.
            now: Current time (optional, for testing).

        Returns:
            Number of commands executed.
        """
        if now is None:
            now = dt_util.utcnow()

        executed = 0

        for entity_id, should_open, reason in control_state.pending_commands:
            service = SERVICE_OPEN_COVER_TILT if should_open else SERVICE_CLOSE_COVER_TILT

            _LOGGER.debug(
                "Executing %s on %s: %s",
                service,
                entity_id,
                reason,
            )

            try:
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    service,
                    {ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
                self._last_command_times[entity_id] = now
                executed += 1
            except Exception as ex:
                _LOGGER.error(
                    "Failed to execute %s on %s: %s",
                    service,
                    entity_id,
                    ex,
                )

        return executed

    def get_summary(
        self, control_state: VentControlState
    ) -> dict[str, Any]:
        """Get a summary of the vent control state.

        Args:
            control_state: The current VentControlState.

        Returns:
            Dict with summary information.
        """
        areas_summary = {}
        for area_id, area_state in control_state.area_states.items():
            areas_summary[area_id] = {
                "area_name": area_state.area_name,
                "should_open": area_state.should_open,
                "open_reason": area_state.open_reason,
                "total_vents": area_state.total_vent_count,
                "open_vents": area_state.open_vent_count,
                "vents": [
                    {
                        "entity_id": v.entity_id,
                        "is_group": v.is_group,
                        "member_count": v.member_count,
                        "is_open": v.is_open,
                        "should_be_open": v.should_be_open,
                        "open_reason": v.open_reason,
                    }
                    for v in area_state.vents
                ],
            }

        return {
            "total_vents": control_state.total_vents,
            "open_vents": control_state.open_vents,
            "vents_should_be_open": control_state.vents_should_be_open,
            "min_vents_required": self._min_vents_open,
            "pending_commands": len(control_state.pending_commands),
            "areas": areas_summary,
        }
