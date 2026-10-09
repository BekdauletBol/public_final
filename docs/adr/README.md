# Architecture decision records

One file per decision, `NNNN-short-title.md`, sections: Context, Decision,
Alternatives, Consequences. Status is Accepted unless stated.

| # | Title | Milestone |
|---|---|---|
| 0001 | [Python 3.12, uv-managed venv, pinned freeze with headless OpenCV override](0001-toolchain-and-pinning.md) | M0 |

Required by the specification (Section O.2) and written when the decision is implemented:
SQLite default with Postgres option (M1); in-process sliding-window rate limiter (M1);
per-camera mailbox and round-robin scheduler (M3); first-message WebSocket auth (M2);
opaque device tokens instead of JWT (M2); client-side overlay rendering (M4);
rules computed for all variants every frame (M4); AGPL implications of ultralytics
and the `Detector` seam (M4).
