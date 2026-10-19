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
- **Offline evaluation** (in progress): the same pipeline on MOT17 and
  recorded sequences with pre-registered metrics.

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
make lint           # ruff, black, mypy (strict on core), eslint, prettier, tsc
```

218 automated tests in total:

| Suite | Tests | Runner |
|---|---|---|
| Unit (geometry, rules with Hypothesis, state machine, mailbox, pairing, config) | 136 | `make test` |
| Integration (API, WebSockets, pipeline, migrations, scripted fake phone) | 63 | `make test` |
| Security (login rate limit and timing, PIN redeem limits, secrets never in responses or logs) | 8 | `make test` |
| Computer vision (scripted sequences to events, zone payloads, recorder, golden image; 2 need the real weights) | 8 | `make test`, `make test-model` |
| End-to-end in Chromium (pairing, zones) | 3 | `make test-browser` |
| Load (1 scenario, 3 fake phones at 15 fps, latest vs FIFO) | 1 | `make test-load` |

To run the migration round-trip against PostgreSQL:
`docker compose up -d db`, then
`PUBLICSAFE_TEST_PG_URL=postgresql+psycopg://publicsafe:publicsafe@127.0.0.1:55432/publicsafe make test`.

## Project status

| Milestone | Scope | Status |
|---|---|---|
| M0 | Repository scaffold, toolchain pinning, HTTPS dev setup | Done |
| M1 | Database, migrations, admin auth, rate limiting, sessions/cameras/zones API | Done |
| M2 | PIN pairing, device credentials, camera and dashboard WebSockets, dashboard UI | Done |
| M3 | Frame ingest, mailbox scheduler, phone capture page, metrics, load test, E2E suite | Done |
| M4 | YOLOv8n detection, six membership rules, state machine, zone editor, overlays, events, CSV | Done |
| M5 | Evaluation harness: datasets, metrics, rule comparison, latency benchmark | In progress |
| M6 | Structured logging, API reference, demo checklist, soak test | Planned |

Known limitations:

- One inference worker and one YOLOv8n model per laptop; throughput is shared
  by all cameras (about 50 ms per frame on an Apple M-series GPU).
- No person tracking: counts are per frame, so identities are not followed
  across frames. Tracking is a planned extension behind the detector seam.
- Tested with one iPhone (Safari); Android Chrome is supported by design but
  has not been verified on a device.
- Frames are processed in memory only. Nothing is recorded unless the
  evaluation recorder is switched on explicitly.
- Evaluation results (precision, recall, F1 per rule; latency benchmark) are
  not available yet; they are the subject of M5.
- Phones need the mkcert root certificate installed once, which is a manual
  step on iOS.

## Documentation

| Document | Content |
|---|---|
| [docs/SETUP_MACOS.md](docs/SETUP_MACOS.md) | Prerequisites, one-time setup, running, tests, troubleshooting |
| [docs/PHONE_SETUP.md](docs/PHONE_SETUP.md) | Trusting the root certificate on iOS and Android, manual phone checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Repository layout, phones, detection, load test, database, configuration |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Model checksum, FP16 measurement, evaluation plan and success criteria |
| [docs/adr/README.md](docs/adr/README.md) | Architecture decision records 0001 to 0009 |
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
