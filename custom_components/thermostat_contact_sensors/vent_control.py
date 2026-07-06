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
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverEntityFeature
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
IGNORED_ROOM_PRIORITY_PENALTY = 6000.0

# Service names for tilt control
SERVICE_OPEN_COVER_TILT = "open_cover_tilt"
SERVICE_CLOSE_COVER_TILT = "close_cover_tilt"
SERVICE_OPEN_COVER = "open_cover"
SERVICE_CLOSE_COVER = "close_cover"


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
    closed_vents: int = 0
    ignored_closed_vents: int = 0
    effective_min_vents_open: int = 0
    max_closed_vents: int | None = None
    safety_budget_exceeded: bool = False
    area_states: dict[str, AreaVentState] = field(default_factory=dict)
    pending_commands: list[tuple[str, bool, str]] = field(
        default_factory=list
    )  # (entity_id, should_open, reason)


class VentController:
    """Controller for managing HVAC vents based on room state."""

    @staticmethod
    def _pending_is_unresponsive(
        *,
        current_state: bool,
        desired_state: bool,
        elapsed_seconds: float,
        retry_count: int,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
    ) -> bool:
        return (
            current_state != desired_state
            and elapsed_seconds >= timeout_seconds
            and retry_count >= max_retries
        )

    def _get_unresponsive_vents(self, *, now: datetime) -> set[str]:
        """Return vents considered unresponsive based on pending confirmations."""
        unresponsive_vents: set[str] = set()
        for entity_id, (
            desired_state,
            command_time,
            retry_count,
        ) in self._pending_confirmations.items():
            elapsed = (now - command_time).total_seconds()
            current_state = self.get_vent_current_state(entity_id)
            if self._pending_is_unresponsive(
                current_state=current_state,
                desired_state=desired_state,
                elapsed_seconds=elapsed,
                retry_count=retry_count,
            ):
                unresponsive_vents.add(entity_id)
        return unresponsive_vents

    @staticmethod
    def infer_effective_hvac_mode(
        room_temp_states: dict[str, "RoomTemperatureState"],
        target_temp_low: float | None,
        target_temp_high: float | None,
    ) -> HVACMode | None:
        """Infer whether we're closer to needing heat or cooling.

        When HVAC is off (idle), we look at all temperature sensors across
        all areas and determine whether on average we're closer to needing
        heating or cooling. This is used for intelligent vent prioritization
        during shoulder seasons (spring/fall) when HVAC bounces between modes.

        Args:
            room_temp_states: Dict of area_id -> RoomTemperatureState with sensor readings.
            target_temp_low: The heating target temperature (target_temp_low for auto mode).
            target_temp_high: The cooling target temperature (target_temp_high for auto mode).

        Returns:
            HVACMode.HEAT if we're closer to needing heat,
            HVACMode.COOL if we're closer to needing cooling,
            None if we can't determine (no readings or no targets).
        """
        if target_temp_low is None or target_temp_high is None:
            return None

        # Collect a single representative temperature per area.
        # Prefer the HVAC-aware determining_temperature (which is what the rest of
        # the integration uses for decisions), and fall back to raw sensor readings
        # only when determining_temperature is unavailable.
        all_temps: list[float] = []
        for room_state in room_temp_states.values():
            temp = getattr(room_state, "determining_temperature", None)
            if temp is not None:
                all_temps.append(temp)
                continue

            sensor_readings = getattr(room_state, "sensor_readings", None)
            if sensor_readings:
                readings = list(sensor_readings.values())
                all_temps.append(sum(readings) / len(readings))

        if not all_temps:
            return None

        # Calculate average temperature across all sensors
        avg_temp = sum(all_temps) / len(all_temps)

        # Calculate distance to each target
        # Positive distance_to_heat means we're below heating target (need heat)
        # Positive distance_to_cool means we're above cooling target (need cool)
        distance_to_heat = target_temp_low - avg_temp  # Positive if cold
        distance_to_cool = avg_temp - target_temp_high  # Positive if hot

        _LOGGER.debug(
            "Infer HVAC mode: avg_temp=%.2f, target_low=%.2f, target_high=%.2f, "
            "distance_to_heat=%.2f, distance_to_cool=%.2f",
            avg_temp,
            target_temp_low,
            target_temp_high,
            distance_to_heat,
            distance_to_cool,
        )

        # If we're in the comfort zone (between targets), use whichever
        # boundary we're closer to
        if distance_to_heat <= 0 and distance_to_cool <= 0:
            # We're within the comfort band - compare absolute distances to boundaries
            if abs(distance_to_heat) < abs(distance_to_cool):
                # Closer to heating threshold, prioritize as if heating
                return HVACMode.HEAT
            else:
                # Closer to cooling threshold, prioritize as if cooling
                return HVACMode.COOL
        elif distance_to_heat > 0:
            # We're below heating target - need heat
            return HVACMode.HEAT
        else:
            # We're above cooling target - need cool
            return HVACMode.COOL

    @staticmethod
    def _calculate_temperature_need(
        *,
        determining_temperature: float | None,
        effective_mode: HVACMode | None,
        target_temp_low: float | None,
        target_temp_high: float | None,
        distance_from_target: float | None,
        fallback_to_distance: bool,
    ) -> float | None:
        """Compute temperature "need" for vent decisions.

        Positive = needs conditioning in the effective mode.
        Negative = already on the helped side of the target.

        Returns None when we have no usable determining temperature.
        """
        if determining_temperature is None:
            return None

        temp = determining_temperature

        if effective_mode == HVACMode.HEAT and target_temp_low is not None:
            return target_temp_low - temp
        if effective_mode == HVACMode.COOL and target_temp_high is not None:
            return temp - target_temp_high

        if fallback_to_distance and distance_from_target is not None:
            return distance_from_target

        return 0.0

    def __init__(
        self,
        hass: HomeAssistant,
        min_vents_open: int = 5,
        max_closed_vents: int | None = 3,
        vent_open_delay_seconds: int = 30,
        vent_debounce_seconds: int = 30,
    ) -> None:
        """Initialize the vent controller.

        Args:
            hass: Home Assistant instance.
            min_vents_open: Minimum number of vents that must remain open.
            max_closed_vents: Maximum number of physical vents that may be closed.
            vent_open_delay_seconds: Seconds after occupancy before vents open.
            vent_debounce_seconds: Minimum time between vent state changes.
        """
        self.hass = hass
        self._min_vents_open = self._normalize_vent_count(min_vents_open)
        self._max_closed_vents = self._normalize_optional_vent_count(max_closed_vents)
        self._vent_open_delay_seconds = vent_open_delay_seconds
        self._vent_debounce_seconds = vent_debounce_seconds

        # Track last command time per vent for debouncing
        self._last_command_times: dict[str, datetime] = {}

        # Track pending commands that haven't been confirmed
        # Maps entity_id -> (desired_state, command_time, retry_count)
        self._pending_confirmations: dict[str, tuple[bool, datetime, int]] = {}

        # Track vent states
        self._vent_states: dict[str, VentState] = {}

    @staticmethod
    def _normalize_vent_count(value: Any, default: int = 0) -> int:
        """Return a non-negative integer vent count."""
        try:
            normalized = int(round(float(value)))
        except (TypeError, ValueError):
            normalized = default
        return max(0, normalized)

    @classmethod
    def _normalize_optional_vent_count(cls, value: Any) -> int | None:
        """Return an optional non-negative integer vent count."""
        if value is None:
            return None
        return cls._normalize_vent_count(value)

    @property
    def min_vents_open(self) -> int:
        """Return minimum vents that must remain open."""
        return self._min_vents_open

    @min_vents_open.setter
    def min_vents_open(self, value: int) -> None:
        """Set minimum vents that must remain open."""
        self._min_vents_open = self._normalize_vent_count(value)

    @property
    def max_closed_vents(self) -> int | None:
        """Return maximum vents that may be closed."""
        return self._max_closed_vents

    @max_closed_vents.setter
    def max_closed_vents(self, value: int | None) -> None:
        """Set maximum vents that may be closed."""
        self._max_closed_vents = self._normalize_optional_vent_count(value)

    def effective_min_vents_open(self, total_vents: int) -> int:
        """Return the effective open-vent minimum for the current total."""
        total_vents = self._normalize_vent_count(total_vents)
        min_required = self._min_vents_open
        if self._max_closed_vents is not None:
            min_required = max(min_required, total_vents - self._max_closed_vents)
        return min(total_vents, max(0, min_required))

    @classmethod
    def two_thirds_min_vents_open(cls, total_vents: int) -> int:
        """Return the conservative two-thirds open smart-vent recommendation."""
        total_vents = cls._normalize_vent_count(total_vents)
        return min(total_vents, (total_vents * 2 + 2) // 3)

    def max_closed_min_vents_open(self, total_vents: int) -> int | None:
        """Return the minimum implied by max_closed_vents, if configured."""
        total_vents = self._normalize_vent_count(total_vents)
        if self._max_closed_vents is None:
            return None
        return min(total_vents, max(0, total_vents - self._max_closed_vents))

    def get_safety_warnings(self, control_state: VentControlState) -> list[str]:
        """Return live vent safety warnings for diagnostics."""
        warnings: list[str] = []
        total_vents = self._normalize_vent_count(control_state.total_vents)
        if total_vents <= 0:
            return warnings

        max_closed_min = self.max_closed_min_vents_open(total_vents)
        if (
            max_closed_min is not None
            and control_state.open_vents < max_closed_min
        ):
            warnings.append(
                f"Only {control_state.open_vents} of {total_vents} physical vents are open; "
                f"max_closed_vents={self._max_closed_vents} requires at least {max_closed_min} open."
            )

        two_thirds_min = self.two_thirds_min_vents_open(total_vents)
        if control_state.open_vents < two_thirds_min:
            warnings.append(
                f"Only {control_state.open_vents} of {total_vents} physical vents are open; "
                f"two-thirds smart-vent guidance recommends at least {two_thirds_min} open."
            )

        return warnings

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

    def get_cover_service(self, entity_id: str, should_open: bool) -> str:
        """Return the best cover service for a vent entity."""
        state = self.hass.states.get(entity_id)
        if state is None:
            return SERVICE_OPEN_COVER_TILT if should_open else SERVICE_CLOSE_COVER_TILT

        if "supported_features" not in state.attributes:
            return SERVICE_OPEN_COVER_TILT if should_open else SERVICE_CLOSE_COVER_TILT

        supported = state.attributes.get("supported_features", 0)

        tilt_feature = (
            CoverEntityFeature.OPEN_TILT if should_open else CoverEntityFeature.CLOSE_TILT
        )
        if supported & tilt_feature:
            return SERVICE_OPEN_COVER_TILT if should_open else SERVICE_CLOSE_COVER_TILT

        return SERVICE_OPEN_COVER if should_open else SERVICE_CLOSE_COVER

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
        hvac_mode: HVACMode | None = None,
        target_temp_low: float | None = None,
        target_temp_high: float | None = None,
        unresponsive_vents: set[str] | None = None,
        force_open_reason: str | None = None,
        now: datetime | None = None,
    ) -> AreaVentState:
        """Evaluate vent states for an area.

        Policy: vents are forced-open only for (eligible) critical rooms.
        All other openings are decided by minimum-vent selection in
        `evaluate_all_vents()`.

        Args:
            area_id: The area ID.
            area_name: The area name.
            vents: List of vent entity IDs for this area.
            is_active: Deprecated for vent decisions (kept for compatibility).
            is_occupied: Deprecated for vent decisions (kept for compatibility).
            is_satiated: Deprecated for vent decisions (kept for compatibility).
            is_critical: Whether the room should be forced open by policy.
            occupancy_start_time: Deprecated for vent decisions (kept for compatibility).
            distance_from_target: How far from target temperature (for prioritization).
            determining_temperature: Actual temperature for HVAC-aware priority sorting.
            area_vent_open_delay: Deprecated for vent decisions (kept for compatibility).
            hvac_mode: Effective HVAC mode (unused for forced-open decisions).
            target_temp_low: Heating target temperature (unused for forced-open decisions).
            target_temp_high: Cooling target temperature (unused for forced-open decisions).
            unresponsive_vents: Vents to suppress commands for.
            force_open_reason: Reason to store when the area is forced open.
            now: Current time (optional, for testing).

        Returns:
            AreaVentState with evaluated vent states.
        """
        if now is None:
            now = dt_util.utcnow()

        area_state = AreaVentState(
            area_id=area_id,
            area_name=area_name,
            occupancy_start_time=occupancy_start_time,
            distance_from_target=distance_from_target,
            determining_temperature=determining_temperature,
        )

        should_open = bool(is_critical)
        if should_open:
            open_reason: str | None = (
                force_open_reason if force_open_reason is not None else "Critical temperature"
            )
        else:
            open_reason = None

        area_state.should_open = should_open
        area_state.open_reason = open_reason

        # Evaluate each vent
        for vent_entity_id in vents:
            is_group = self.is_cover_group(vent_entity_id)
            member_count = self.get_group_member_count(vent_entity_id)
            is_open = self.get_vent_current_state(vent_entity_id)

            # Unresponsive vents should not be commanded open/closed until recovered.
            should_be_open = should_open
            vent_open_reason = open_reason if should_open else None
            if unresponsive_vents and vent_entity_id in unresponsive_vents:
                should_be_open = False
                vent_open_reason = "Unresponsive vent"

            vent_state = VentState(
                entity_id=vent_entity_id,
                area_id=area_id,
                is_group=is_group,
                member_count=member_count,
                is_open=is_open,
                should_be_open=should_be_open,
                last_command_time=self._last_command_times.get(vent_entity_id),
                open_reason=vent_open_reason,
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
        room_temp_states: dict[str, "RoomTemperatureState"] | None = None,
        target_temp_low: float | None = None,
        target_temp_high: float | None = None,
        eco_mode: bool = False,
        only_track_selected_rooms: bool = False,
        tracked_area_ids: set[str] | None = None,
        force_track_when_critical_area_ids: set[str] | None = None,
        excluded_area_ids: set[str] | None = None,
    ) -> list[tuple[str, str, int, float]]:
        """Calculate priority order for keeping minimum vents open.

        When we need to keep vents open for back pressure prevention, we prioritize:
        1. (Eco ON + TSR ON) Critical tracked rooms + critical force-track-when-critical rooms
        2. (Eco ON + TSR ON) Critical untracked rooms
        3. Highest temperature need for the current effective mode

        Rooms with no usable temperature signal are treated as the *lowest* priority
        for minimum-vent selection.

        When HVAC mode is OFF or unknown, we infer whether we're closer to
        needing heat or cooling based on all temperature sensor readings,
        rather than using absolute distance from target.

        Args:
            area_states: Dict of area_id -> AreaVentState.
            hvac_mode: Current HVAC mode for temperature-aware sorting.
            room_temp_states: Dict of area_id -> RoomTemperatureState (for inferring mode).
            target_temp_low: Heating target temperature (for inferring mode).
            target_temp_high: Cooling target temperature (for inferring mode).
            eco_mode: Whether eco mode is enabled.
            only_track_selected_rooms: Whether TSR is enabled.
            tracked_area_ids: Set of tracked room area_ids (when TSR enabled).
            force_track_when_critical_area_ids: Set of rooms with FTCR enabled.
            excluded_area_ids: Rooms that should close first unless the safety
                budget requires them to stay open.

        Returns:
            List of (area_id, vent_entity_id, member_count, priority_score).
            Higher score = higher priority for staying open.
        """

        priority_list: list[tuple[str, str, int, float]] = []

        tracked_area_ids = tracked_area_ids or set()
        force_track_when_critical_area_ids = force_track_when_critical_area_ids or set()
        excluded_area_ids = excluded_area_ids or set()

        # Determine effective HVAC mode for prioritization
        effective_mode = hvac_mode
        if hvac_mode in (None, HVACMode.OFF) and room_temp_states:
            # HVAC is off/idle - infer whether we're closer to needing heat or cool
            inferred_mode = self.infer_effective_hvac_mode(
                room_temp_states, target_temp_low, target_temp_high
            )
            if inferred_mode:
                effective_mode = inferred_mode
                _LOGGER.debug(
                    "HVAC is %s, inferred effective mode: %s",
                    hvac_mode,
                    effective_mode,
                )

        for area_id, area_state in area_states.items():
            is_excluded = area_id in excluded_area_ids

            for vent in area_state.vents:
                priority_score = 0.0

                if is_excluded:
                    priority_score -= IGNORED_ROOM_PRIORITY_PENALTY

                # Critical-tracking priority. When Eco+TSR is on, we want critical tracked
                # and FTCR rooms to win, then critical untracked, then everything else.
                temp_state = room_temp_states.get(area_id) if room_temp_states else None
                is_critical = bool(getattr(temp_state, "is_critical", False))
                if is_critical:
                    if eco_mode and only_track_selected_rooms:
                        if (
                            area_id in tracked_area_ids
                            or area_id in force_track_when_critical_area_ids
                        ):
                            priority_score += 2_000_000.0
                        else:
                            priority_score += 1_000_000.0
                    else:
                        priority_score += 1_000_000.0

                # Areas with no determining temperature are last-resort for minimum-vent selection.
                if area_state.determining_temperature is None:
                    priority_score -= 5000.0

                # Temperature-based priority.
                # Use the relevant target (heat low / cool high) when available so we
                # don't prefer rooms that are already over-target in HEAT or under-target in COOL.
                if area_state.determining_temperature is not None:
                    temp = area_state.determining_temperature

                    # Compute deviation in the direction we care about.
                    # Positive = needs conditioning for the current effective mode.
                    need = 0.0
                    if effective_mode == HVACMode.HEAT and target_temp_low is not None:
                        need = target_temp_low - temp
                    elif effective_mode == HVACMode.COOL and target_temp_high is not None:
                        need = temp - target_temp_high
                    elif area_state.distance_from_target is not None:
                        # If we don't have usable targets (or mode), fall back to absolute distance.
                        need = area_state.distance_from_target

                    # Reward being on the wrong side of the target, penalize being on the
                    # "already helped" side.
                    if need > 0:
                        priority_score += need * 200.0
                    else:
                        priority_score += need * 20.0

                    # Treat large deviations as "critical" for the purposes of minimum-vent selection.
                    # This is deliberately conservative: it only affects ranking among *minimum* vents.
                    if need >= 3.0:
                        priority_score += 3000.0
                elif area_state.distance_from_target is not None:
                    priority_score += area_state.distance_from_target * 10.0

                priority_list.append(
                    (area_id, vent.entity_id, vent.member_count, priority_score)
                )

        # Sort by priority score descending
        priority_list.sort(key=lambda x: x[3], reverse=True)
        return priority_list

    def _apply_closed_vent_budget(
        self,
        *,
        control_state: VentControlState,
        priority_list: list[tuple[str, str, int, float]],
        unresponsive_vents: set[str],
        excluded_area_ids: set[str],
    ) -> None:
        """Apply the global closed-vent safety budget to all non-forced vents."""
        effective_min = self.effective_min_vents_open(control_state.total_vents)
        control_state.max_closed_vents = self._max_closed_vents
        control_state.effective_min_vents_open = effective_min

        if control_state.total_vents <= 0:
            return

        allowed_closed_vents = max(control_state.total_vents - effective_min, 0)
        priority_by_entity = {
            vent_entity_id: priority_score
            for _, vent_entity_id, _, priority_score in priority_list
        }

        forced_open: set[str] = set()
        selected_closed: set[str] = set()
        candidates: list[tuple[float, int, int, str, str, VentState]] = []
        closed_used = 0

        for area_id, area_state in control_state.area_states.items():
            for vent in area_state.vents:
                if vent.should_be_open:
                    forced_open.add(vent.entity_id)
                    continue

                if vent.entity_id in unresponsive_vents:
                    selected_closed.add(vent.entity_id)
                    closed_used += vent.member_count
                    continue

                is_excluded = area_id in excluded_area_ids
                priority_score = priority_by_entity.get(vent.entity_id, 0.0)
                candidates.append(
                    (
                        priority_score,
                        0 if is_excluded else 1,
                        vent.member_count,
                        area_id,
                        vent.entity_id,
                        vent,
                    )
                )

        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]))

        for _, _, member_count, _, vent_entity_id, _ in candidates:
            if closed_used + member_count > allowed_closed_vents:
                continue
            selected_closed.add(vent_entity_id)
            closed_used += member_count

        closed_budget_minimum = (
            control_state.total_vents - self._max_closed_vents
            if self._max_closed_vents is not None
            else None
        )
        safety_reason = (
            f"Closed vent budget (max {self._max_closed_vents} closed)"
            if (
                closed_budget_minimum is not None
                and closed_budget_minimum > self._min_vents_open
            )
            else f"Minimum vents (need {effective_min})"
        )
        for area_state in control_state.area_states.values():
            for vent in area_state.vents:
                if vent.entity_id in forced_open:
                    continue
                if vent.entity_id in selected_closed:
                    continue
                vent.should_be_open = True
                vent.open_reason = safety_reason

    def evaluate_all_vents(
        self,
        area_vent_configs: dict[str, list[str]],
        active_areas: list["AreaOccupancyState"],
        occupied_areas: list["AreaOccupancyState"],
        room_temp_states: dict[str, "RoomTemperatureState"] | None = None,
        area_vent_delays: dict[str, int] | None = None,
        hvac_mode: HVACMode | None = None,
        target_temp_low: float | None = None,
        target_temp_high: float | None = None,
        eco_mode: bool = False,
        only_track_selected_rooms: bool = False,
        tracked_area_ids: set[str] | None = None,
        force_track_when_critical_area_ids: set[str] | None = None,
        excluded_area_ids: set[str] | None = None,
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
            target_temp_low: Heating target temperature (for inferring mode when HVAC off).
            target_temp_high: Cooling target temperature (for inferring mode when HVAC off).
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

        # Use a single effective HVAC mode for vent decisions.
        effective_mode = hvac_mode
        if hvac_mode in (None, HVACMode.OFF) and room_temp_states:
            inferred_mode = self.infer_effective_hvac_mode(
                room_temp_states, target_temp_low, target_temp_high
            )
            if inferred_mode is not None:
                effective_mode = inferred_mode

        # Build lookup sets
        active_area_ids = {a.area_id for a in active_areas}
        occupied_area_ids = {a.area_id for a in occupied_areas}

        tracked_area_ids = tracked_area_ids or set()
        force_track_when_critical_area_ids = force_track_when_critical_area_ids or set()
        excluded_area_ids = excluded_area_ids or set()

        # Build occupancy start time lookup
        occupancy_times: dict[str, datetime | None] = {}
        for area in occupied_areas:
            occupancy_times[area.area_id] = area.occupancy_start_time
        for area in active_areas:
            if area.area_id not in occupancy_times:
                occupancy_times[area.area_id] = area.occupancy_start_time

        # Track which vents are unresponsive (pending for >60s with 3+ retries).
        unresponsive_vents = self._get_unresponsive_vents(now=now)

        # Evaluate each area
        for area_id, vents in area_vent_configs.items():
            if not vents:
                continue

            is_active = area_id in active_area_ids
            is_occupied = area_id in occupied_area_ids
            is_excluded = area_id in excluded_area_ids
            occupancy_start_time = occupancy_times.get(area_id)
            area_delay = area_vent_delays.get(area_id, self._vent_open_delay_seconds)
            vent_open_delay_elapsed = True
            if area_delay and occupancy_start_time is not None:
                vent_open_delay_elapsed = (now - occupancy_start_time).total_seconds() >= area_delay

            # Get temperature state for this area. Ignored rooms still keep their
            # temperature data for closure-safety ranking, but cannot force open.
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

            # Decide whether this critical room is eligible to be force-open.
            # - Eco OFF: force-open all critical rooms.
            # - Eco ON + TSR OFF: force-open all critical rooms.
            # - Eco ON + TSR ON: force-open critical tracked rooms + critical FTCR rooms.
            is_force_open_critical = False
            if is_critical and not is_excluded:
                if eco_mode and only_track_selected_rooms:
                    is_force_open_critical = (
                        area_id in tracked_area_ids
                        or area_id in force_track_when_critical_area_ids
                    )
                else:
                    is_force_open_critical = True

            # Also force-open active rooms that are unsatiated.
            # - Eco OFF: any active unsatiated room.
            # - Eco ON + TSR OFF: any active unsatiated room.
            # - Eco ON + TSR ON: active unsatiated rooms that are tracked by TSR.
            is_force_open_active_unsatiated = False
            if (
                is_active
                and not is_excluded
                and temp_state is not None
                and not temp_state.is_satiated
                and determining_temperature is not None
                and vent_open_delay_elapsed
            ):
                if eco_mode and only_track_selected_rooms:
                    is_force_open_active_unsatiated = area_id in tracked_area_ids
                else:
                    is_force_open_active_unsatiated = True

            force_open = is_force_open_critical or is_force_open_active_unsatiated
            force_open_reason: str | None = None
            if force_open:
                if is_force_open_critical:
                    force_open_reason = "Critical temperature"
                elif eco_mode and only_track_selected_rooms:
                    force_open_reason = "Active (tracked) unsatiated"
                else:
                    force_open_reason = "Active unsatiated"

            area_state = self.evaluate_area_vents(
                area_id=area_id,
                area_name=area_name,
                vents=vents,
                is_active=is_active,
                is_occupied=is_occupied,
                is_satiated=is_satiated,
                is_critical=force_open,
                occupancy_start_time=occupancy_start_time,
                distance_from_target=distance_from_target,
                determining_temperature=determining_temperature,
                area_vent_open_delay=area_vent_delays.get(area_id),
                hvac_mode=effective_mode,
                target_temp_low=target_temp_low,
                target_temp_high=target_temp_high,
                unresponsive_vents=unresponsive_vents,
                force_open_reason=force_open_reason,
                now=now,
            )

            control_state.area_states[area_id] = area_state
            control_state.total_vents += area_state.total_vent_count
            control_state.open_vents += area_state.open_vent_count

        priority_list = self.calculate_minimum_vents_priority(
            control_state.area_states,
            hvac_mode=effective_mode,
            room_temp_states=room_temp_states,
            target_temp_low=target_temp_low,
            target_temp_high=target_temp_high,
            eco_mode=eco_mode,
            only_track_selected_rooms=only_track_selected_rooms,
            tracked_area_ids=tracked_area_ids,
            force_track_when_critical_area_ids=force_track_when_critical_area_ids,
            excluded_area_ids=excluded_area_ids,
        )
        self._apply_closed_vent_budget(
            control_state=control_state,
            priority_list=priority_list,
            unresponsive_vents=unresponsive_vents,
            excluded_area_ids=excluded_area_ids,
        )

        # Calculate final count
        control_state.vents_should_be_open = 0
        control_state.closed_vents = 0
        control_state.ignored_closed_vents = 0
        for area_state in control_state.area_states.values():
            for vent in area_state.vents:
                if vent.should_be_open:
                    control_state.vents_should_be_open += vent.member_count
                else:
                    control_state.closed_vents += vent.member_count
                    if area_state.area_id in excluded_area_ids:
                        control_state.ignored_closed_vents += vent.member_count
        control_state.safety_budget_exceeded = (
            control_state.vents_should_be_open < control_state.effective_min_vents_open
        )

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
        """Execute pending vent commands and track confirmation.

        Args:
            control_state: The VentControlState with pending commands.
            now: Current time (optional, for testing).

        Returns:
            Number of commands executed.
        """
        if now is None:
            now = dt_util.utcnow()

        executed = 0

        # First, check for unconfirmed commands from previous runs.
        # If a vent hasn't changed state after 60 seconds, retry up to 3 times,
        # then mark it as unresponsive.
        unresponsive_vents: set[str] = set()
        for entity_id, (
            desired_state,
            command_time,
            retry_count,
        ) in list(self._pending_confirmations.items()):
            elapsed = (now - command_time).total_seconds()
            current_state = self.get_vent_current_state(entity_id)

            if current_state == desired_state:
                del self._pending_confirmations[entity_id]
                _LOGGER.debug("Vent %s confirmed in desired state", entity_id)
                continue

            if elapsed < 60:
                continue

            if self._pending_is_unresponsive(
                current_state=current_state,
                desired_state=desired_state,
                elapsed_seconds=elapsed,
                retry_count=retry_count,
            ):
                unresponsive_vents.add(entity_id)
                del self._pending_confirmations[entity_id]
                _LOGGER.error(
                    "Vent %s marked unresponsive after 3 retries",
                    entity_id,
                )
            elif retry_count < 3:
                _LOGGER.warning(
                    "Vent %s hasn't responded after %.0fs (retry %d/3)",
                    entity_id,
                    elapsed,
                    retry_count + 1,
                )

        for entity_id, should_open, reason in control_state.pending_commands:
            # Skip unresponsive vents
            if entity_id in unresponsive_vents:
                _LOGGER.debug("Skipping command for unresponsive vent %s", entity_id)
                continue

            service = self.get_cover_service(entity_id, should_open)

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
                
                # Track this command for confirmation
                retry_count = 0
                if entity_id in self._pending_confirmations:
                    _, _, retry_count = self._pending_confirmations[entity_id]
                self._pending_confirmations[entity_id] = (should_open, now, retry_count + 1)
                
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
            "closed_vents": control_state.closed_vents,
            "ignored_closed_vents": control_state.ignored_closed_vents,
            "min_vents_required": self._min_vents_open,
            "effective_min_vents_open": control_state.effective_min_vents_open,
            "max_closed_min_vents_open": self.max_closed_min_vents_open(
                control_state.total_vents
            ),
            "two_thirds_min_vents_open": self.two_thirds_min_vents_open(
                control_state.total_vents
            ),
            "max_closed_vents": control_state.max_closed_vents,
            "safety_budget_exceeded": control_state.safety_budget_exceeded,
            "safety_warnings": self.get_safety_warnings(control_state),
            "pending_commands": len(control_state.pending_commands),
            "areas": areas_summary,
        }
