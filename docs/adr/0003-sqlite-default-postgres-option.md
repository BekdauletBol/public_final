# ADR 0003: SQLite by default, PostgreSQL by `DATABASE_URL`

Status: Accepted (M1, 2026-10-10)

## Context

The system runs on one laptop with a handful of cameras; events and metric
samples are small rows written at most a few times per second. The spec
requires a one-command local setup and CI-ready tests, with PostgreSQL as an
option.

## Decision

- Default `DATABASE_URL=sqlite+pysqlite:///data/publicsafe.db` (path relative
  to `<root>`), opened with `journal_mode=WAL`, `synchronous=NORMAL` and
  `foreign_keys=ON` on every connection (`db/base.py`).
- PostgreSQL via `postgresql+psycopg://...`. Portable column types are chosen
  once: ids are `String(36)` with a native `UUID` variant on PostgreSQL, JSON
  columns use `JSON` with a `JSONB` variant, timestamps go through
  `UTCDateTime`, which stores UTC and always returns aware datetimes (SQLite
  drops the offset).
- Alembic from the first migration (`0001_initial_schema`), rendered in batch
  mode so downgrades work on SQLite; `AUTO_MIGRATE=true` upgrades at start,
  otherwise the server refuses to start on a schema mismatch.
- `docker-compose.yml` provides PostgreSQL 18 on host port 55432 (5432 was
  taken by a local PostgreSQL on the development Mac);
  `PUBLICSAFE_TEST_PG_URL` enables the migration round-trip test against it.

## Alternatives

- PostgreSQL only: fails the "no setup beyond make" requirement.
- SQLite only: blocks multi-process deployments later.

## Consequences

- Every migration must work on both backends; the round-trip test runs on
  SQLite always and on PostgreSQL when the container is up.
- The single uvicorn process plus the worker thread share one SQLite file;
  WAL keeps readers from blocking the writer.
