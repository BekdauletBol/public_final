# Architecture decision records

One file per decision, `NNNN-short-title.md`, sections: Context, Decision,
Alternatives, Consequences. Status is Accepted unless stated.

| # | Title | Milestone |
|---|---|---|
| 0001 | [Python 3.12, uv-managed venv, pinned freeze with headless OpenCV override](0001-toolchain-and-pinning.md) | M0 |
| 0002 | [In-process sliding-window rate limiter instead of slowapi](0002-in-process-rate-limiter.md) | M1 |
| 0003 | [SQLite by default, PostgreSQL by DATABASE_URL](0003-sqlite-default-postgres-option.md) | M1 |
| 0004 | [WebSocket authentication by first message, after accept](0004-first-message-websocket-auth.md) | M2 |
| 0005 | [Opaque random device tokens instead of JWT](0005-opaque-device-tokens.md) | M2 |
| 0006 | [Per-camera latest-frame mailbox with a round-robin scheduler](0006-per-camera-mailbox-round-robin.md) | M3 |
| 0007 | [Overlays are rendered client-side from JSON](0007-client-side-overlays.md) | M4 |
| 0008 | [All membership rules computed every frame](0008-all-rules-every-frame.md) | M4 |
| 0009 | [AGPL-3.0 ultralytics behind the Detector seam](0009-ultralytics-agpl-and-detector-seam.md) | M4 |

All ADRs required by the specification (Section O.2) are written.
