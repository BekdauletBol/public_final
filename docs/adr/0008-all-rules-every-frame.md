# ADR 0008: All membership rules are computed for every detection, every frame

Status: Accepted (M4, 2026-10-10)

## Context

The research question compares foot-anchored rules with bounding-box overlap.
Only one rule drives the zone's state machine (`active_rule`), but the
comparison needs the others on the same frames and detections.

## Decision

`core/rules.py` evaluates all six rules (`bbox_overlap`, `bottom_center`,
`three_point_any`, `three_point_majority`, `three_point_all`, `bottom_strip`)
for every detection and enabled zone on every frame. `frame_result` carries
`rule_hits` and `counts` per rule; every event stores `rule_counts`; the CSV
export has one column per rule. The invariants
`all ⊆ majority ⊆ any ⊆ bbox_overlap` and `bottom_center ⊆ any` are
property-tested and can be asserted by the evaluation run.

## Alternatives

- Computing only the active rule: cheaper by microseconds, but the offline
  comparison would need a second pass over recorded frames.

## Consequences

- Rule cost is a few prepared-polygon tests per detection (`rules_ms` is
  reported in timings; sub-millisecond for typical counts).
- Changing `active_rule` never changes what is recorded, only which counts
  drive events, so experiments can be re-run from stored data.
