# public.safe

**public.safe** turns ordinary smartphones into camera nodes for monitoring
pedestrian danger zones (a kerb, a loading bay, the edge of a platform). A
phone's browser streams JPEG frames over a secure WebSocket to a laptop. The
laptop runs a YOLOv8n person detector, tests each detected person's
foot-contact point against a polygon drawn on the camera image, turns
per-frame membership into *entered* / *cleared* events with hysteresis, and
shows everything live on a web dashboard. No app store, no dedicated cameras,
no cloud: everything runs on one machine in the local network, and no video
is ever stored.

It is built for a diploma project at Astana IT University. The intended users
are safety staff and researchers who need a cheap, quickly deployable way to
count people inside a marked zone and to study *which* geometric rule
(foot-point versus bounding-box overlap) agrees best with human judgement.
The same detection and rule code runs offline on public datasets, so the
results are reproducible.

## Key features

- **Phones as cameras.** Pair a phone with a short PIN or a QR code; the
  browser page captures the camera and streams JPEG frames. Nothing is
  installed on the phone.
- **Secure by default.** HTTPS and WSS with locally issued certificates,
  Argon2id admin password, opaque device tokens, rate limiting, PIN
  brute-force lockout, strict Content-Security-Policy.
- **Per-camera mailbox scheduler.** Each camera keeps only its latest frame;
  a single inference worker serves cameras round-robin, so a slow phone never
  delays the others and memory stays bounded.
- **YOLOv8n person detection** with automatic device selection (Apple
  Silicon `mps`, CUDA, or CPU) and a recorded model checksum.
- **Six zone membership rules** computed every frame: foot-point variants
  (bottom-centre, any bottom point, two of three, all three, bottom strip)
  and plain bounding-box overlap.
- **Events with hysteresis.** A per-zone state machine emits exactly one
  `zone_entered` and one `zone_cleared` per visit, with configurable enter and
  clear frame counts.
- **Live dashboard.** Sessions, camera tiles, live frame with overlays, polygon
  zone editor, events table with filters and CSV export, metrics, health and
  security pages. Dark and light themes.
- **Offline evaluation.** The same detector and rules on MOT17, image folders,
  videos and recorded sessions: Hungarian matching, per-rule precision /
  recall / F1 with bootstrap CIs, McNemar tests, boundary analysis, event
  metrics with a hysteresis sweep, and a Markdown report with figures.
- **Structured logs.** One JSON object per line with request and camera ids;
  every response carries `X-Request-ID`.

## Architecture

```mermaid
flowchart LR
    subgraph Phone["Phone (browser)"]
        CAM["getUserMedia<br/>JPEG capture loop"]
    end
    subgraph Mac["Laptop (FastAPI, Python 3.12)"]
        WS["WSS ingest<br/>first-message auth"]
        MB["Per-camera mailbox<br/>latest frame, round-robin"]
        DET["YOLOv8n worker<br/>person detection"]
        RULES["Foot-point zone rules<br/>6 rules every frame"]
        SM["Zone state machine<br/>hysteresis"]
        DB[("SQLite / PostgreSQL<br/>sessions, cameras, zones, events")]
        HUB["Dashboard fan-out<br/>status, previews, events"]
    end
    subgraph Browser["Dashboard (React 19)"]
        UI["Live view, zone editor,<br/>events, metrics"]
    end
    CAM -- "wss:// binary JPEG" --> WS
    WS --> MB --> DET --> RULES --> SM
    SM -- "zone_entered / zone_cleared" --> DB
    SM --> HUB
    DET -- "detections, overlays (JSON)" --> HUB
    HUB -- "wss:// JSON" --> UI
    UI -- "REST /api" --> DB
```

Read left to right: the phone sends frames, the mailbox keeps only the newest
per camera, the worker detects people, the rules decide membership, the state
machine debounces it into events, and the dashboard receives overlays and
events over its own WebSocket. Design decisions are recorded as
[ADRs](docs/adr/README.md).

## Screenshots

All screenshots were taken in Chromium by the Playwright suite against the
test backend; the green picture is Chromium's synthetic camera, which stands
in for a phone.

| | |
|---|---|
| ![Session page with a live camera tile and metrics](docs/screenshots/m3/m3-session-live.png) | ![Zone editor drawing a polygon on the live frame](docs/screenshots/m4/m4-zone-editor.png) |
| *Session page: live camera tile, status, per-camera metrics.* | *Zone editor: polygon drawn on the live frame, rule and hysteresis settings.* |
| ![Camera page with zone overlay and two events](docs/screenshots/m4/m4-camera-events.png) | ![Events page with filters and CSV export](docs/screenshots/m4/m4-events.png) |
| *Camera page: zone overlay, one entered and one cleared event, per-rule counts.* | *Events page: filters by session, camera, type and time; CSV export.* |
| ![PIN pairing modal with QR code, dark theme](docs/screenshots/m2/dark-pin-modal.png) | ![Phone page while streaming](docs/screenshots/m3/m3-phone-streaming.png) |
| *Pairing: PIN, link and QR code (dark theme).* | *Phone page while streaming: connection state, send rate, last acknowledgement.* |

<!--
More screenshots (add here):
| ![caption](docs/screenshots/<file>.png) | ![caption](docs/screenshots/<file>.png) |
| *caption* | *caption* |
-->

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI 0.143, Uvicorn, SQLAlchemy 2.1, Alembic, Pydantic 2 |
| Computer vision | Ultralytics YOLOv8n 8.4, PyTorch 2.14, OpenCV (headless), Shapely, NumPy |
| Database | SQLite (WAL) by default, PostgreSQL optional |
| Frontend | React 19, TypeScript 6, Vite 8, TanStack Query 5, React Router 7, plain CSS tokens |
| Security | mkcert certificates, Argon2id, opaque device tokens, CSP, in-process rate limiter |
| Testing | pytest, Hypothesis, Playwright (Chromium), custom load test, ruff, black, mypy, eslint, prettier |
| Tooling | uv, Node 24, Make, Docker Compose (PostgreSQL only) |

## Quick start (macOS, Apple Silicon)

```bash
brew install uv mkcert                    # one-time tools
make setup                                # Python 3.12.13 venv, pinned deps, npm ci, .env
mkcert -install                           # one-time: trust the dev root CA on this Mac
make certs                                # certificate for localhost, LAN IPs, <host>.local
.venv/bin/publicsafe admin set-password   # choose the admin password (stored as a hash in .env)
make dev                                  # API on https://localhost:8443, dashboard on https://localhost:5173
```

In plain words: install the two tools, let `make setup` create the Python
environment and install the pinned dependencies, trust the local certificate
authority once, issue a certificate, set the admin password, and start the
development servers. Log in at `https://localhost:5173`.

`make run` builds the frontend and serves everything from
`https://<lan-ip>:8443`, which is how the demo runs. Phones must trust the
mkcert root certificate once; see [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md)
(also served at `/help/phone`). The full walkthrough and troubleshooting are in
[docs/SETUP_MACOS.md](docs/SETUP_MACOS.md); day-to-day operating notes
(detection settings, database, load test, configuration) are in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Testing

```bash
make test           # pytest: unit, integration, security, cv (no network, no GPU, fake detector)
make test-model     # the real YOLOv8n on a golden image, FP16 vs FP32 timing (downloads weights once)
make test-browser   # Playwright end-to-end tests in Chromium against a test-mode backend
make test-load      # fake phones against a live server; asserts scheduler fairness and memory bound
make test-soak      # 8-hour soak (SOAK_DURATION=seconds): memory growth <= 20 %, no crash
make lint           # ruff, black, mypy (strict on core), eslint, prettier, tsc
make api-docs       # regenerate docs/API.md from the code (a test fails if it is stale)
```

270 automated tests in total:

| Suite | Tests | Runner |
|---|---|---|
| Unit (geometry, rules with Hypothesis, state machine, mailbox, pairing, config, eval metrics and datasets, logs, API reference) | 167 | `make test` |
| Integration (API, WebSockets, pipeline, migrations, scripted fake phone, eval CLI and annotation page) | 70 | `make test` |
| Security (login rate limit and timing, PIN redeem limits, secrets never in responses or logs) | 8 | `make test` |
| Computer vision (scripted sequences to events, recorder, 100-frame synthetic eval with hand-computed numbers, golden image; 2 need the real weights) | 20 | `make test`, `make test-model` |
| End-to-end in Chromium (pairing, zones) | 3 | `make test-browser` |
| Load (3 fake phones at 15 fps, latest vs FIFO; `LOADTEST_DETECTOR=yolo` for the real model) | 1 | `make test-load` |
| Soak (3 fake phones at 10 fps for 8 h, memory sampled every 30 s) | 1 | `make test-soak` |

To run the migration round-trip against PostgreSQL:
`docker compose up -d db`, then
`PUBLICSAFE_TEST_PG_URL=postgresql+psycopg://publicsafe:publicsafe@127.0.0.1:55432/publicsafe make test`.

## Running the experiments

The research questions are answered by seven pre-registered experiments
(E1-E7, spec I.5). Commands, success criteria and the results measured so
far are in [docs/EVALUATION.md](docs/EVALUATION.md); in short:

```bash
.venv/bin/publicsafe eval fetch mot17      # MOT17-02, -04, -09 from the official archive (~465 MB)
.venv/bin/publicsafe eval annotate --sequence data/mot17/train/MOT17-04 \
  --zone-file data/zones/MOT17-04.json --zone strip --every 5 --annotator <name>
make eval CONFIG=configs/e1_mot17.yaml SWEEP="enter_frames=1,3,5 exit_frames=1,5,10"   # E1, E2, E3
make test-load                             # E5: latest-frame mailbox vs FIFO
```

The zone files in `data/zones/` are placeholders until the owner draws the
real zones; `configs/e1_mot17_derived.yaml` runs the same evaluation on
automatically derived ground truth as a sanity check.

## Troubleshooting

| Problem | Fix |
|---|---|
| Certificate warning on the Mac | `mkcert -install`, restart the browser |
| Certificate warning on the phone | Install and trust the root CA ([docs/PHONE_SETUP.md](docs/PHONE_SETUP.md)); after the Mac changes network run `publicsafe certs --force` |
| Phone says the camera is denied | iOS: Settings > Safari > Camera > Allow; Android Chrome: lock icon > Permissions > Camera; the page must be `https://` |
| "MPS unavailable" | Intel Mac or old macOS: inference falls back to CPU; lower the camera's target fps (5) and max long edge (960) |
| "Refusing to start" | The message names the fix (`publicsafe admin set-password`, `make certs`) |

More in [docs/SETUP_MACOS.md](docs/SETUP_MACOS.md#5-troubleshooting).

## Project status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Repository scaffold, toolchain pinning, HTTPS dev setup | Done |
| M1 | Database, migrations, admin auth, rate limiting, sessions/cameras/zones API | Done |
| M2 | PIN pairing, device credentials, camera and dashboard WebSockets, dashboard UI | Done |
| M3 | Frame ingest, mailbox scheduler, phone capture page, metrics, load test, E2E suite | Done |
| M4 | YOLOv8n detection, six membership rules, state machine, zone editor, overlays, events, CSV | Done |
| M5 | Evaluation harness: datasets, metrics, rule comparison, latency benchmark | Done (headline run waits for the owner's zones and manual labels) |
| M6 | Structured logging, API reference, demo checklist, soak test | Done (phone demo items and the full 8-hour soak are manual, see [DEMO_CHECKLIST](docs/DEMO_CHECKLIST.md)) |

Known limitations:

- One inference worker and one YOLOv8n model per laptop; throughput is shared
  by all cameras (15-18 ms inference and 21-27 ms decode + inference + rules
  per 1080p frame on an M1 GPU; 3 phones at 10 fps keep p99 below 70 ms).
- No person tracking: counts are per frame, so identities are not followed
  across frames. Tracking is a planned extension behind the detector seam.
- Tested with one iPhone (Safari); Android Chrome is supported by design but
  has not been verified on a device.
- Frames are processed in memory only. Nothing is recorded unless the
  evaluation recorder is switched on explicitly.
- The evaluation numbers so far come from MOT17 with placeholder zones and
  *derived* ground truth (GT box bottom-centre in the zone), which favours the
  foot-point rules: `bottom_center` precision 0.92 against 0.45 for box
  overlap at similar recall. The headline comparison needs manual labels on
  the owner's zones and a second data source ([EVALUATION.md](docs/EVALUATION.md)).
- YOLOv8n at 640 px finds only 41-45 % of the people in the crowded, distant
  MOT17-02 and -04 scenes (88 % in the close-up MOT17-09), so person-level
  recall is limited by the detector, not by the zone rules.
- Tracking (experiment E4) is not implemented; `eval run --track` stops with
  an error.
- Phones need the mkcert root certificate installed once, which is a manual
  step on iOS.

## Documentation

| Document | Content |
|---|---|
| [docs/SETUP_MACOS.md](docs/SETUP_MACOS.md) | Prerequisites, one-time setup, running, tests, troubleshooting |
| [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md) | Trusting the root certificate on iOS and Android, manual phone checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Repository layout, phones, detection, load test, database, configuration |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Evaluation harness, datasets and checksums, experiments E1-E7, success criteria and results |
| [docs/results/](docs/results/) | Committed evaluation reports (`report.md`, `results.csv`, `metrics.json`, figures) |
| [docs/API.md](docs/API.md) | REST endpoints, schemas and WebSocket messages (generated from the code) |
| [docs/DEMO_CHECKLIST.md](docs/DEMO_CHECKLIST.md) | Definition of Done: what was run, real outputs, what is left for real phones |
| [docs/adr/README.md](docs/adr/README.md) | Architecture decision records 0001 to 0011 |
| [docs/IMPLEMENTATION_SPEC.md](docs/IMPLEMENTATION_SPEC.md) | The full implementation specification the project is built from |
| [docs/diagrams/README.md](docs/diagrams/README.md) | Architecture diagrams: context, use case, component, data model, sequence; PlantUML sources and PNG/SVG exports |
| [docs/report/SE-2405_Bolatov_ProbPeriod1.pdf](docs/report/SE-2405_Bolatov_ProbPeriod1.pdf) | Probation period 1 report |
| [.env.example](.env.example) | Every configuration variable with its default |

## Author

**Bolatov Bekdaulet Almasovich (Болатов Бекдаулет Алмасович)**, Astana IT University.
Supervisor: **Nurgaliyeva Symbat Altybayevna (Нургалиева Сымбат Алтыбаевна)**.

## License

This project is licensed under the GNU Affero General Public License v3.0,
see [LICENSE](LICENSE). The person detector depends on the AGPL-3.0
[Ultralytics](https://github.com/ultralytics/ultralytics) package;
[ADR 0009](docs/adr/0009-ultralytics-agpl-and-detector-seam.md) explains the
consequences and the seam that would allow a permissively licensed detector.

# ps
##
## Project history and repository timeline

This project continues the student research work "Edge-distributed pedestrian
monitoring system using YOLOv8 and geometric spatial analysis", 2nd place at the
AITU student research conference (НИРС), 2026. The prototype described in that
work was developed and evaluated before this repository was created.

For the diploma stage the system was rewritten against the diploma requirements
and imported into this repository on 10 October 2026 as milestone-scoped commits
(M0–M4). The task board was created on 18 October 2026 and documents those
milestones retrospectively: each issue records the expected outcome, acceptance
criteria and the commit hashes that implement it.

From milestone M5 onwards every work item is opened on the board before the work
starts, developed in a task branch and merged through a pull request.
