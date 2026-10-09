# ADR 0004: WebSocket authentication by first message, after accept

Status: Accepted (M2, 2026-10-10)

## Context

Phones connect to `/ws/camera` with a device token; dashboards connect to
`/ws/dashboard` with the admin cookie. Browsers cannot set custom headers on
WebSocket upgrades, query strings land in access logs, and a socket refused
before `accept()` surfaces to JavaScript only as code 1006 with no reason.

## Decision

- `/ws/camera`: the server accepts the socket, then waits at most
  `WS_AUTH_TIMEOUT_S` (5 s) for a JSON `auth` message carrying the token. The
  token is hashed and looked up; unknown or malformed → close 4001, revoked
  credential/camera or archived session → close 4003. The token therefore
  never appears in a URL, header or log line.
- `/ws/dashboard`: the admin cookie is checked after `accept()`; failure sends
  an `error` message and closes with 4001 so the page can redirect to login.
- One live socket per camera: a new successful `auth` closes the previous one
  with 4004 (`superseded`) and takes over, which handles a reloading phone.
- Every close initiated by the server is preceded by an `error` message with a
  machine-readable `code`, and the status transition to `offline` is skipped
  for sockets the server closed itself (revoked, replaced, superseded, deleted).

## Alternatives

- Token in the query string: logged by proxies and uvicorn's access log.
- `Sec-WebSocket-Protocol` smuggling: works in browsers but is a hack and still
  visible in upgrade logs.
- Closing before accept: loses the close code in browsers.

## Consequences

- An unauthenticated client can hold a socket for up to 5 s; acceptable on a LAN.
- The test suite exercises every close code with a scripted phone
  (`tests/integration/fake_phone.py`).
