# public.safe

Ordinary smartphones become camera nodes for pedestrian danger-zone monitoring.
A phone's browser streams JPEG frames over a secure WebSocket to a laptop; the
laptop runs a YOLOv8n person detector, tests each person's foot-contact points
against a per-camera polygon, turns per-frame membership into entry/exit events
with hysteresis, and shows everything live on a web dashboard. The same code
runs offline on public datasets for reproducible evaluation.

Status: **M1** (HTTPS, database and migrations, admin login, sessions, cameras
and zones CRUD, dashboard login/sessions pages). Pairing, streaming and detection
arrive in M2-M4; see `publicsafe-cli-spec.md`.

## Quick start (macOS, Apple Silicon)

```bash
brew install uv mkcert          # one-time tools
make setup                      # Python 3.12.13 venv, pinned deps, npm ci, .env
mkcert -install                 # one-time: trust the dev root CA on this Mac
make certs                      # certificate for localhost, LAN IPs, <host>.local
.venv/bin/publicsafe admin set-password   # choose the admin password (written to .env as a hash)
make dev                        # https://localhost:8443 API + Vite on :5173 (log in there)
```

`make run` builds the frontend and serves everything from `https://<lan-ip>:8443`.
Phones must trust the mkcert root CA once: see [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md)
(served at `/help/phone`). Full setup and troubleshooting:
[docs/SETUP_MACOS.md](docs/SETUP_MACOS.md).

## Layout

```
backend/publicsafe/   FastAPI app, core CV logic, worker, db, eval CLI
backend/tests/        unit, integration, security, cv, load
frontend/             Vite + React 19 + TypeScript dashboard and phone pages
docs/                 setup, phone CA, API, evaluation, ADRs
configs/              evaluation configs
data/, certs/         git-ignored runtime data and certificates
```

## Commands

`make help` lists every target: `setup`, `lock`, `certs`, `dev`, `run`, `build`,
`test`, `test-model`, `test-browser`, `test-load`, `eval`, `lint`, `format`, `clean`.

## Database

SQLite at `data/publicsafe.db` by default (WAL mode), migrated automatically at
start. PostgreSQL: `docker compose up -d db` and set
`DATABASE_URL=postgresql+psycopg://publicsafe:publicsafe@127.0.0.1:55432/publicsafe`.
Migrations live in `backend/publicsafe/migrations/` (Alembic; `cd backend && ../.venv/bin/alembic ...`).

## Configuration

All variables are documented in [.env.example](.env.example) and declared in
`backend/publicsafe/config.py`. The server refuses to start without
`ADMIN_PASSWORD_HASH` and tells you the command to run.
