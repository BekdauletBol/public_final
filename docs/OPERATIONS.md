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
CLI checks and prints the instruction. `LOADTEST_DETECTOR=yolo` runs the same
test with the real model, `LOADTEST_SOURCE=<dir>` streams your own JPEGs and
`LOADTEST_FPS` / `LOADTEST_PHONES` change the load.

## Soak test

```bash
make test-soak                          # 8 hours, 3 fake phones at 10 fps
SOAK_DURATION=3600 make test-soak       # 1 hour
```

The server's RSS is sampled every 30 s (`SOAK_SAMPLE_S`); the test fails if
memory grows by more than 20 % after the warm-up (`SOAK_WARMUP_S`, default
10 % of the run, at most 10 minutes), if the server dies, or if fairness goes
above 1.3. Samples, the server log and a summary land in
`data/soak/<timestamp>/`.

## Evaluation

```bash
.venv/bin/publicsafe eval fetch mot17                    # MOT17-02, -04, -09 (~465 MB)
.venv/bin/publicsafe eval annotate --sequence data/mot17/train/MOT17-04 \
  --zone-file data/zones/MOT17-04.json --zone strip --every 5 --annotator bb
make eval CONFIG=configs/e1_mot17.yaml SWEEP="enter_frames=1,3,5 exit_frames=1,5,10"
```

Outputs go to `data/eval/<run_id>/` (`report.md`, `results.csv`,
`metrics.json`, `per_frame.jsonl`, `figures/`). How every experiment is run
and the results so far are in [EVALUATION.md](EVALUATION.md).

## Logs

The server writes one JSON object per line to stderr (`LOG_JSON=false` for
plain text). Every line carries `request_id` for HTTP / WebSocket work and
`camera_id` for a phone's socket and its frames; each HTTP request also gets
an access line (`publicsafe.access`: method, path without query string,
status, `duration_ms`) and an `X-Request-ID` response header. Examples:

```bash
make run 2>&1 | jq -cR 'fromjson? | select(.level != "info")'               # warnings and errors
make run 2>&1 | jq -cR 'fromjson? | select(.camera_id == "<camera id>")'    # one phone
```

PINs, tokens, cookies, passwords and frames are never logged.

## Commands

`make help` lists every target: `setup`, `lock`, `certs`, `dev`, `run`, `build`,
`test`, `test-model`, `test-browser`, `test-load`, `test-soak`, `eval`, `api-docs`,
`lint`, `format`, `clean`. The REST and WebSocket reference is
[API.md](API.md) (regenerate with `make api-docs` after changing a route).

## Database

SQLite at `data/publicsafe.db` by default (WAL mode), migrated automatically at
start. PostgreSQL: `docker compose up -d db` and set
`DATABASE_URL=postgresql+psycopg://publicsafe:publicsafe@127.0.0.1:55432/publicsafe`.
Migrations live in `backend/publicsafe/migrations/` (Alembic; `cd backend && ../.venv/bin/alembic ...`).

## Configuration

All variables are documented in [.env.example](../.env.example) and declared in
`backend/publicsafe/config.py`. The server refuses to start without
`ADMIN_PASSWORD_HASH` and tells you the command to run.
