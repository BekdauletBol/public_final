"""M4: hysteresis state machine sequences from Section L and H.4."""

from __future__ import annotations

from publicsafe.core.state_machine import ZoneStateMachine


def run(machine: ZoneStateMachine, counts: list[int], dt_ms: float = 100.0) -> list[str]:
    out = []
    for i, c in enumerate(counts):
        out += [f"{e.type}:{e.count_before}>{e.count_after}" for e in machine.step(c, i * dt_ms)]
    return out


def test_one_frame_blip_no_event() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    assert run(m, [0, 1, 0, 0, 0, 0]) == []
    assert m.state == "clear"


def test_three_frames_enter_then_exit_after_five() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    events = run(m, [1, 1, 1, 0, 0, 0, 0, 0])
    assert events == ["zone_entered:0>1", "zone_cleared:1>0"]
    assert m.state == "clear" and m.debounced_count == 0


def test_flicker_stays_occupied_until_exit_frames() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    events = run(m, [1, 1, 1, 0, 1, 0, 1, 0, 1, 0])
    assert events == ["zone_entered:0>1"]
    assert m.state == "occupied"
    events = run(m, [0, 0, 0, 0, 0])
    assert events == ["zone_cleared:1>0"]


def test_count_change_emits_once_after_enter_frames() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    events = run(m, [1, 1, 1, 2, 2, 2, 2, 2, 1, 1, 1, 1])
    assert events == ["zone_entered:0>1", "occupancy_changed:1>2", "occupancy_changed:2>1"]
    assert m.debounced_count == 1


def test_unstable_count_change_does_not_emit() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    events = run(m, [1, 1, 1, 2, 3, 2, 3, 2, 3])
    assert events == ["zone_entered:0>1"]


def test_entering_frame_does_not_run_occupied_block() -> None:
    m = ZoneStateMachine(enter_frames=1, exit_frames=1)
    events = run(m, [2])
    assert events == ["zone_entered:0>2"] and m.debounced_count == 2


def test_gap_reset_keeps_state() -> None:
    m = ZoneStateMachine(enter_frames=3, exit_frames=5)
    m.step(1, 0)
    m.step(1, 100)
    m.step(1, 200)
    assert m.state == "occupied"
    m.step(0, 300)
    m.step(0, 400)
    assert m.consecutive_out == 2
    m.step(0, 5000)  # > 2 s gap: counters reset, state kept, nothing fabricated
    assert m.state == "occupied" and m.consecutive_out == 1 and m.debounced_count == 1
    m.step(1, 5100)
    m.step(1, 5200)
    events = run(m, [1, 1], 1.0)
    assert events == [] and m.state == "occupied"


def test_disabled_freezes_everything() -> None:
    m = ZoneStateMachine(enter_frames=1, exit_frames=1)
    m.step(1, 0)
    assert m.state == "occupied"
    m.configure(1, 1, enabled=False)
    assert m.step(0, 100) == [] and m.state == "occupied"
    m.configure(1, 1, enabled=True)
    assert [e.type for e in m.step(0, 200)] == ["zone_cleared"]


def test_identical_for_truth_and_prediction_paths() -> None:
    seq = [0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0, 2, 2, 2, 2]
    a = run(ZoneStateMachine(3, 5), seq)
    b = run(ZoneStateMachine(3, 5), seq)
    assert a == b == ["zone_entered:0>1", "zone_cleared:1>0", "zone_entered:0>2"]
