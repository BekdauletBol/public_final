# public.safe

Ordinary smartphones become camera nodes for pedestrian danger-zone monitoring.
A phone's browser streams JPEG frames over a secure WebSocket to a laptop; the
laptop runs a YOLOv8n person detector, tests each person's foot-contact points
against a per-camera polygon, turns per-frame membership into entry/exit events
with hysteresis, and shows everything live on a web dashboard. The same code
runs offline on public datasets for reproducible evaluation.

Status: **M3** (HTTPS, database, admin login, sessions/cameras/zones CRUD, PIN
pairing, phone capture page, frame ingest with a per-camera mailbox, live
previews, metrics, load test). Detection, zones and events arrive in M4; see
`publicsafe-cli-spec.md`.

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

## Phones

Pair a phone from a session's "Add camera" button: it shows a PIN, a link and a
QR. On the phone open `https://<lan-ip>:5173/pair` (dev) or `:8443/pair`
(`make run`), type the PIN and allow the camera. The phone page shows the
connection state, send rate and last acknowledgement; "Forget this phone"
removes the pairing.

## Load test

```bash
make test-load                          # 3 fake phones at 15 fps for 60 s, latest then fifo
LOADTEST_DURATION=15 make test-load     # quicker
.venv/bin/publicsafe loadtest --phones 1,2,3,5 --fps 10 --source data/frames \
  --duration 60 --server https://localhost:8443 --admin-password ... --ca "$(mkcert -CAROOT)/rootCA.pem"
```

`--queue fifo` requires the server to run with `SCHEDULER_STRATEGY=fifo`; the
CLI checks and prints the instruction.

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
