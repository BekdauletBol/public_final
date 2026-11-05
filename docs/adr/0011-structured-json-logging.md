# ADR 0011: Structured JSON logs with stdlib logging and context variables

Status: Accepted (M6, 2026-11-05)

## Context

Section D asks for structured JSON logs with request ids and camera ids.
Until M6 the server used plain `logging` calls with uvicorn's default text
format; `LOG_JSON` existed in the config but did nothing. The log must never
contain PINs, tokens, cookies, passwords or frame contents (O.2).

## Decision

- `publicsafe/logs.py` installs one handler on the root logger with a JSON
  formatter (one object per line: `t`, `level`, `logger`, `msg`, ids and any
  `extra=` fields). `LOG_JSON=false` prints the same fields as text.
- Ids come from `contextvars`: a pure-ASGI middleware sets `request_id` for
  every HTTP request and WebSocket (and returns it as `X-Request-ID`; a
  client's own id is kept only if it is 1-64 safe characters); the camera
  socket and the inference worker set `camera_id`. Code does not have to pass
  ids around.
- The middleware writes one access line per request with method, path,
  status and duration. The query string is never logged. uvicorn's own
  access log is turned off and its other loggers propagate to the same
  handler (`log_config=None`).
- stdlib `logging` rather than `structlog` calls: every library (uvicorn,
  SQLAlchemy, alembic, ultralytics) already logs through it, so one formatter
  covers all of them without wrapping.

## Alternatives

- `structlog` everywhere: nicer API, but third-party loggers would still need
  a stdlib bridge, and every existing call would change.
- Logging the full URL: useful for debugging, but query strings may carry
  secrets in the future; the path is enough.

## Consequences

- Logs can be filtered with `jq`, e.g.
  `jq -R 'fromjson? | select(.camera_id=="<id>")'` for one phone.
- The existing security tests (PIN and token never in captured logs) keep
  working; `tests/unit/test_logs.py` checks the format, the ids, the
  `X-Request-ID` header and that query strings stay out.
