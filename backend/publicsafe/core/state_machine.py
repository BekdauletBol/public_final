"""Zone occupancy state machine with hysteresis (H.4). Pure and deterministic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EventType = Literal["zone_entered", "zone_cleared", "occupancy_changed"]
State = Literal["clear", "occupied"]

#: A gap longer than this between frames resets the consecutive counters (H.4).
GAP_RESET_MS = 2000.0


@dataclass(frozen=True)
class ZoneEvent:
    type: EventType
    t_ms: float
    count_before: int
    count_after: int


@dataclass
class ZoneStateMachine:
    enter_frames: int = 3
    exit_frames: int = 5
    enabled: bool = True
    state: State = "clear"
    debounced_count: int = 0
    consecutive_in: int = 0
    consecutive_out: int = 0
    consecutive_change: int = 0
    candidate_count: int = 0
    t_last_ms: float | None = None
    frames: int = field(default=0)

    def configure(self, enter_frames: int, exit_frames: int, enabled: bool) -> None:
        """Apply new settings; the current state is kept (F-ZONE-3)."""
        self.enter_frames = enter_frames
        self.exit_frames = exit_frames
        self.enabled = enabled

    def reset_counters(self) -> None:
        self.consecutive_in = self.consecutive_out = self.consecutive_change = 0
        self.candidate_count = 0

    def step(self, raw_count: int, t_ms: float) -> list[ZoneEvent]:
        """Feed one frame's count for the active rule; returns the events it produced."""
        if not self.enabled:
            return []
        if self.t_last_ms is not None and t_ms - self.t_last_ms > GAP_RESET_MS:
            self.reset_counters()  # a disconnect never fabricates a zone_cleared
        self.t_last_ms = t_ms
        self.frames += 1
        events: list[ZoneEvent] = []

        if self.state == "clear":
            self.consecutive_in = self.consecutive_in + 1 if raw_count >= 1 else 0
            if self.consecutive_in >= self.enter_frames:
                self.state = "occupied"
                self.debounced_count = raw_count
                self.consecutive_out = 0
                self.consecutive_change = 0
                events.append(ZoneEvent("zone_entered", t_ms, 0, raw_count))
            return events  # the entering frame does not also run the occupied block

        self.consecutive_out = self.consecutive_out + 1 if raw_count == 0 else 0
        if self.consecutive_out >= self.exit_frames:
            self.state = "clear"
            events.append(ZoneEvent("zone_cleared", t_ms, self.debounced_count, 0))
            self.debounced_count = 0
            self.consecutive_in = 0
            self.consecutive_change = 0
            return events

        if raw_count >= 1 and raw_count != self.debounced_count:
            if raw_count == self.candidate_count:
                self.consecutive_change += 1
            else:
                self.candidate_count = raw_count
                self.consecutive_change = 1
            if self.consecutive_change >= self.enter_frames:
                events.append(ZoneEvent("occupancy_changed", t_ms, self.debounced_count, raw_count))
                self.debounced_count = raw_count
                self.consecutive_change = 0
        else:
            self.consecutive_change = 0
        return events
