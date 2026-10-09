# Architecture decision records

One file per decision, `NNNN-short-title.md`, sections: Context, Decision,
Alternatives, Consequences. Status is Accepted unless stated.

| # | Title | Milestone |
|---|---|---|
| 0001 | [Python 3.12, uv-managed venv, pinned freeze with headless OpenCV override](0001-toolchain-and-pinning.md) | M0 |
| 0002 | [In-process sliding-window rate limiter instead of slowapi](0002-in-process-rate-limiter.md) | M1 |
| 0003 | [SQLite by default, PostgreSQL by DATABASE_URL](0003-sqlite-default-postgres-option.md) | M1 |

Required by the specification (Section O.2) and written when the decision is implemented:
per-camera mailbox and round-robin scheduler (M3); first-message WebSocket auth (M2);
opaque device tokens instead of JWT (M2); client-side overlay rendering (M4);
rules computed for all variants every frame (M4); AGPL implications of ultralytics
and the `Detector` seam (M4).
