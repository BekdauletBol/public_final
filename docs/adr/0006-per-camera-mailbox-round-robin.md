# ADR 0006: Per-camera latest-frame mailbox with a round-robin scheduler

Status: Accepted (M3, 2026-10-10)

## Context

Several phones send JPEG frames faster than one YOLOv8n worker can process
them. The paper's prototype used a single `queue.Queue(maxsize=1)` shared by
all phones, so a fast phone could starve the others and every queued frame
was already stale when processed. The research question E5 asks whether a
per-camera latest-frame mailbox bounds tail latency compared with a FIFO.

## Decision

- `core/mailbox.py`: one `LatestFrameSlot` per camera; `put()` replaces an
  unread frame and counts it as `dropped_replaced`; `take()` clears the slot.
- `Scheduler("latest")` visits cameras round-robin, so with K active cameras
  each gets one turn per round regardless of its send rate. The single
  `InferenceWorker` thread blocks on `Scheduler.next(timeout)`.
- `Scheduler("fifo")` keeps the paper's unbounded shared queue for experiment
  E5 only; `SCHEDULER_STRATEGY=fifo` prints a warning at start and the load
  test demonstrates its growing queue wait.
- Frames are handed to the slot on the event loop without decoding; decoding,
  resizing and detection happen on the worker thread.
- Per camera, frames received above 2x the target fps are dropped before the
  slot (`dropped_ratelimit`) and a rate above 3x sustained for
  `RATE_ABUSE_SECONDS` closes the socket with 4008. Both limits count frames
  received, dropped ones included.

## Alternatives

- Bounded FIFO per camera: still serves stale frames and needs a drop policy.
- One worker thread per camera: the GPU/MPS is shared anyway; contention makes
  latency worse and memory grows with cameras.
- Priority by freshness across cameras: more complex and unfair to slow phones.

## Consequences

- Latency is bounded by one round of K processing times; throughput per camera
  is processed fps / K at worst, measured as the fairness ratio.
- `dropped_replaced` is expected and reported, not an error.
- `tests/unit/test_mailbox.py` checks fairness (3 producers at 30 fps,
  consumer at 20 fps, every camera >= 6 fps) and the FIFO's unbounded growth.
