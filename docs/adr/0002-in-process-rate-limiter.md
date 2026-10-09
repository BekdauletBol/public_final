# ADR 0002: In-process sliding-window rate limiter instead of slowapi

Status: Accepted (M1, 2026-10-10; approved by the owner before implementation)

## Context

F-SEC-2 requires per-IP limits on `POST /api/auth/login` (5/min) and
`POST /api/pair/redeem` (10/min and 30/10 min) with a 429 and `Retry-After`.
The spec's reference stack names slowapi 0.1.10, a 2023 wrapper around
`limits` whose compatibility with Starlette 1.7 / FastAPI 0.143 is unverified,
and whose default storage is also in-memory.

## Decision

`publicsafe/api/ratelimit.py` implements `SlidingWindowLimiter`: per key a
deque of hit timestamps, several `(limit, window)` rules evaluated together,
an injectable clock, a lock for the worker threads, and `enforce()` which
raises the project's `ApiError(429)` with `Retry-After` (integer seconds,
rounded up) and `details.retry_after_s`. Rejected requests are not recorded,
so a blocked client is not punished beyond the window. One limiter per route
lives on `app.state.limiters`; the key is the direct peer IP (no proxy
headers are trusted because the deployment is a single LAN host).

## Alternatives

- slowapi: extra dependency, unverified with the pinned framework versions,
  decorator-based API harder to test deterministically.
- Fixed windows: simpler but allow 2x bursts at window edges.
- Redis/external store: excluded by A.4 (no brokers).

## Consequences

- Limits are per process; a restart clears them (acceptable: a 5-minute PIN
  lifetime and the attempt counter on the code still bound an attacker).
- Tests inject a fake clock and never sleep (`tests/unit/test_ratelimit.py`).
- Memory is bounded by (active IPs x longest window x limit) timestamps.
