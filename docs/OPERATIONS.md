# Operations reference

Detailed operating notes that were moved out of the README. For installation
see [SETUP_MACOS.md](SETUP_MACOS.md); for phones see [PHONE_SETUP.md](PHONE_SETUP.md).

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

## Detection

`DETECTOR=yolo` (default) loads YOLOv8n from `data/models/yolov8n.pt`, downloading
it on the first start (needs network once). The device is chosen automatically
(`mps` on Apple Silicon, else `cuda`, else `cpu`) or forced with
`INFERENCE_DEVICE`. `make test-model` runs the real model on a golden image and
prints FP16 vs FP32 timings; `INFERENCE_HALF` stays off unless that run shows a
gain with the same detections.

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

All variables are documented in [.env.example](../.env.example) and declared in
`backend/publicsafe/config.py`. The server refuses to start without
`ADMIN_PASSWORD_HASH` and tells you the command to run.
