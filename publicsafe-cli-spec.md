# public.safe — Implementation Specification for a CLI Coding Agent

**Version:** 1.0 · **Date:** 2026-10-10 · **Audience:** an autonomous CLI coding agent (Claude Code, Codex or similar) building the project from an empty directory on macOS.

**How to read this document.** Statements tagged **[Paper]** are claims from the research paper this project is based on and have *not* been verified; do not treat them as requirements or as facts to reproduce. Statements tagged **[Verified]** were checked against official documentation on 2026-10-10 (or, where noted, by computation). Everything else is a requirement. Where a requirement says **MUST**, deviation needs the owner's approval; **SHOULD** means default unless there is a documented reason.

---

## A. Project overview

### A.1 Purpose

public.safe turns ordinary smartphones into camera nodes for pedestrian danger-zone monitoring. A phone's browser streams JPEG frames over a secure WebSocket to a laptop; the laptop runs a YOLOv8n person detector, tests the detected person's foot-contact points against a polygon ("danger zone") configured per camera, turns per-frame membership into entry/exit events with hysteresis, and shows everything live on a web dashboard. The same code must run offline on public video datasets so the research question can be evaluated reproducibly.

The research question preserved by this build: **does a foot-anchored zone-membership test (bottom-centre or three bottom points of the person box) produce fewer false alarms than bounding-box overlap, and at what cost in recall?** A second question: **does a per-camera latest-frame mailbox bound tail latency under several phones compared with a FIFO queue?**

### A.2 Context from the paper (not requirements)

- [Paper] A prototype existed: one FastAPI file, one `queue.Queue(maxsize=1)` shared by all phones, one hard-coded polygon, YOLOv8n on Apple MPS, a dashboard with Base64 frames, CSV export. None of that code is available. **Assume an empty repository.**
- [Paper] Reported results: Footpoint+PIP precision 0.94 / recall 0.90 / F1 0.92 vs bbox overlap 0.71 / 0.92 / 0.80 on 847 "events"; 145 ms median latency; LIFO p99 380 ms vs FIFO 643 ms with 3 phones. **These numbers are not targets.** [Verified, by computation] The paper's ablation (1-point FP 74 → 3-point FP 20) cannot occur under its stated "any of three points" rule, because the 3-point-any positive set is a superset of the bottom-centre positive set; the rule that produced the numbers is therefore unknown. The evaluation harness in this spec exists to re-establish these results.
- [Paper] Parameters used: JPEG quality 65, 1280×720, 30 fps target, confidence 0.30, class 0 (person) only, camera at ~2 m and 30–45° down, 5 GHz Wi-Fi, MacBook Pro M-series, iPhone iOS 17. These are reasonable defaults and are used below.
- [Paper] The phone demo ran on `http://172.20.10.7:8000`. [Verified] `navigator.mediaDevices.getUserMedia` exists only in secure contexts (https, or localhost); a LAN IP over http is not one. **HTTPS is therefore mandatory for the phone page.**

### A.3 Required outcomes

1. A working local system on macOS: one command to set up, one to run, documented in `README.md`, needing no GitHub account.
2. Admin dashboard: login, create monitoring sessions, mint pairing PINs, see cameras, draw polygon zones on live frames, watch detections and events live, review and export events, see health metrics.
3. Phone page: pair with a PIN, stream the rear camera, reconnect automatically, report status clearly.
4. Backend: secure pairing, per-camera isolation, YOLOv8n detection, all membership rules computed per frame, event state machine with hysteresis, persistence, metrics.
5. Evaluation CLI: run the same detector + rules on a video or image sequence with a zone file and ground truth; output metrics for every rule; produce a report.
6. Tests at unit, integration, security, browser, CV and load level, all runnable locally.

### A.4 Non-goals (do not build)

- Native mobile apps; WebRTC (may be added later behind the same `FrameSource` interface); Redis, Kafka, Celery or any broker; microservices; Kubernetes; cloud deployment; multi-tenant RBAC (single admin is enough); face recognition or any identity inference; ground-plane calibration (P2, out of scope); ESP32 or LED hardware; weather-adaptive thresholds; the paper's survey.

### A.5 Naming

Product name `public.safe`. Python package `publicsafe`. Repository root referred to as `<root>`.

---

## B. Repository inspection instructions (execute before any change)

Even though the owner states the directory is empty, the agent MUST verify and report before writing anything:

1. List `<root>` recursively (ignoring `node_modules`, `.venv`, `.git`). Report whether it is empty.
2. If files exist: identify language/framework (`pyproject.toml`, `requirements*.txt`, `package.json`, `Makefile`, `docker-compose*.yml`), entry points, existing tests, environment variables (`.env*`), CI config, documentation, and any uncommitted user changes (`git status` if a repo exists). Summarise current functionality in ≤ 20 lines.
3. Never delete, rename or overwrite an existing file whose purpose is not understood. If existing code conflicts with this spec, stop and ask.
4. Check the host: `uname -m` (expect `arm64`), `python3 --version`, `node --version`, `mkcert --version`, `brew --version`, `docker --version` (optional). Report which are missing; do not install system packages without saying so (Homebrew installs are acceptable after stating them).
5. Then present the implementation plan (Section O) and wait for approval before Milestone 0.

---

## C. Functional requirements

IDs are referenced by tests and milestones.

### C.1 Administration and sessions

- **F-ADM-1** Single admin account. Credentials come from `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` (argon2id) in `.env`; a CLI command `publicsafe admin set-password` writes the hash. If `ADMIN_PASSWORD_HASH` is missing at startup, the server refuses to start and prints the command to run.
- **F-ADM-2** Login creates a server-side session (random 32-byte id, stored hashed) delivered as an `HttpOnly; Secure; SameSite=Strict` cookie, 12 h idle expiry. Logout invalidates it. Rotating the password invalidates all admin sessions.
- **F-SES-1** Admin can create, list, rename, archive monitoring sessions (`name`, `location_note`, `created_at`, `archived_at`).
- **F-SES-2** A session has 0..N cameras and each camera has 0..N zones (MVP UI supports one zone per camera; the data model supports many).

### C.2 Pairing and cameras

- **F-PAIR-1** Admin mints a pairing PIN for a session. Response includes the PIN, `expires_at`, and the pairing URL. Full protocol in Section G.
- **F-PAIR-2** Phone page `/pair` validates the PIN; on success the backend creates a camera with status `paired`, returns `camera_id` and a device token.
- **F-CAM-1** Camera statuses: `paired` (never connected), `online`, `offline` (no frame or ping for 10 s, detected by a 2 s periodic sweeper, so the transition is visible within 12 s), `paused` (phone reported hidden tab), `error` (worker exception on this camera's last frame; returns to `online` on the next successfully processed frame), `revoked`.
- **F-CAM-2** Admin can rename a camera, change its settings (target fps 1–30 default 10; JPEG quality 30–95 default 65; max long edge 640–1920 default 1280; confidence 0.05–0.95 default 0.30; privacy mode on/off), and revoke it. Settings changes are pushed to the connected phone within 2 s.
- **F-CAM-3** A camera list shows name, status, last-seen, input fps, processed fps, drop ratio, RTT, zone configured yes/no, current occupancy, and `needs_review` (true when any of the camera's zones has `needs_review`).

### C.3 Phone capture page

- **F-PHONE-1** Before asking for a PIN the page checks `window.isSecureContext` and `navigator.mediaDevices`; if either fails it shows an explanation and the exact URL scheme to use. It never shows a PIN form that cannot succeed.
- **F-PHONE-2** After pairing, requests `getUserMedia({video: {facingMode: {ideal: "environment"}, width: {ideal: 1280}, height: {ideal: 720}}, audio: false})`. Shows the local preview, the camera name, connection state, measured send fps and last server ack time.
- **F-PHONE-3** Captures frames at the target fps from the server's `hello`/`settings` message using `requestVideoFrameCallback` where available, else `setInterval`; draws to a canvas scaled to the max long edge; encodes with `canvas.toBlob('image/jpeg', quality)`; sends binary over the WebSocket. Never buffers more than one unsent frame (skip when the socket's `bufferedAmount` exceeds 2 frames' size).
- **F-PHONE-4** Reconnects with exponential backoff (1 s, 2 s, 4 s … max 30 s, ±20 % jitter) using the stored token. Shows "Reconnecting…" with attempt count. On close code 4003 (`revoked`) it stops, deletes the stored token and shows why; on 4004 (`superseded`) it stops and shows "Another tab took over this camera" but keeps the token (the superseding tab shares it).
- **F-PHONE-5** Page Visibility: on `hidden` sends `{type:"pause"}` and stops capture; on `visible` resumes and sends `{type:"resume"}`. Requests a Screen Wake Lock when `navigator.wakeLock` exists, re-requesting on `visibilitychange`.
- **F-PHONE-6** Sends `{type:"ping", t_phone_ms, last_rtt_ms}` every 5 s; expects `pong`; computes `last_rtt_ms` from the previous ping/pong round (null on the first ping).
- **F-PHONE-7** "Forget this phone" button clears the stored token. Token storage: `sessionStorage` by default; `localStorage` if "remember this phone" is ticked. "Remember" is a client-side choice only; the server records the flag in the security log and nothing else.

### C.4 Zones

- **F-ZONE-1** Admin draws a polygon on the latest frame of a camera: click to add vertices, drag to move, double-click/Enter to close, Esc to cancel, Delete to remove vertex. Minimum 3 vertices, maximum 20.
- **F-ZONE-2** Zones are stored in normalised coordinates `[0,1]²` relative to the camera frame, with the frame `width`/`height` recorded at creation. Validation: ≥ 3 points, simple polygon (`shapely.Polygon.is_valid` and non-zero area), all points within `[0,1]`.
- **F-ZONE-3** Zone settings: `name`, `active_rule` (enum in Section H.3, default `bottom_center`), `enter_frames` (default 3), `exit_frames` (default 5), `enabled`. Changes apply to the next processed frame; changing `active_rule` keeps the current state (no synthetic event); `enabled=false` freezes the state machine without emitting events and the zone is drawn grey.
- **F-ZONE-4** If a camera's frame aspect ratio changes by more than 1 %, the zone is flagged `needs_review` and shown with a warning; it is still evaluated.

### C.5 Detection, membership and events

- **F-CV-1** Person detection with YOLOv8n (`classes=[0]`), configurable confidence, device chosen automatically (`mps` → `cuda` → `cpu`) and overridable with `INFERENCE_DEVICE`.
- **F-CV-2** For every detection and every enabled zone, compute **all** membership rules (Section H.3) and record them in the frame result; apply the zone's `active_rule` to the state machine.
- **F-CV-3** Zone state machine per (camera, zone): raw occupancy count per frame → debounced state with `enter_frames` consecutive frames with count ≥ 1 to become `occupied`, `exit_frames` consecutive frames with count 0 to become `clear`. Events: `zone_entered` (clear → occupied), `zone_cleared` (occupied → clear), `occupancy_changed` (debounced count changed while occupied). Each event records camera id, zone id, session id, `t_server` (UTC, ms), frame sequence number, count before/after, `active_rule`, and the per-rule counts for that frame.
- **F-CV-4** Events are persisted and pushed to dashboards within 100 ms of creation.
- **F-CV-5** Dashboard overlays: person boxes with confidence, the three bottom anchor points, the zone polygon (green when clear, red when occupied), per-zone count. Overlays are drawn client-side from JSON; the server never rasterises overlays into frames.

### C.6 Dashboard

- **F-DASH-1** Pages: Login; Sessions list; Session detail (camera grid, PIN modal, events panel, metrics panel); Camera detail (large live view, zone editor, settings, health); Events (table with filters by session/camera/type/time, CSV export); Security (failed pairing attempts, revoked devices); About/Health (server version, device, model hash, uptime).
- **F-DASH-2** Live view via `/ws/dashboard`: subscribe to one or more cameras; receive `frame_result` JSON and, unless privacy mode is on for that camera, the JPEG frame as a binary message. The dashboard chooses a preview rate (default 10 fps, max = camera processed fps).
- **F-DASH-3** Loading, empty and error states for every list and view (see Section J).
- **F-DASH-4** Metrics panel: per camera input fps, processed fps, dropped ratio, RTT, clock offset estimate, inference ms (p50/p95), server span percentiles; server CPU %, memory, device, active cameras.

### C.7 Data export, evaluation and reports

- **F-EXP-1** CSV export of events (filtered) and of per-camera metrics samples; columns documented in Section K.
- **F-EVAL-1** CLI `publicsafe eval run --config <yaml>` runs detection + all rules + the state machine on a video file or an image-sequence directory with a zone JSON and ground-truth file, writing `results.csv`, `per_frame.jsonl`, `metrics.json`, `report.md` (Section I).
- **F-EVAL-2** `EVAL_RECORD=1` makes the live server write each processed frame (JPEG), its `FrameResult` JSON and timings to `data/recordings/<camera_id>/<date>/` so live sessions can be re-evaluated offline. Off by default. The dashboard shows a red "RECORDING" badge when on.
- **F-EVAL-3** CLI `publicsafe loadtest --phones 1,2,3,5 --fps F --source <dir of JPEGs> --duration S --server https://host:8443 --admin-user … --admin-password …` runs against a live server: for each phone count it logs in as admin, mints PINs, redeems them over the real HTTP/WS endpoints, streams the JPEGs at the requested fps, then reads `/api/metrics` and prints per-camera input/processed fps, drop ratio, span percentiles and the **fairness ratio** (max processed fps ÷ min processed fps across cameras). `--queue fifo|latest` is passed to the server through `SCHEDULER_STRATEGY` at server start (the CLI prints the instruction; it cannot switch a running server). Cameras created by the load test are named `loadtest-*` and revoked at the end.

### C.8 Security and privacy (functional)

- **F-SEC-1** All admin API routes require the admin session; all camera WebSocket connections require a valid device token (Section G); `/ws/dashboard` requires the admin session cookie.
- **F-SEC-2** Rate limits (per client IP): `POST /api/pair/redeem` 10/min and 30/10 min; `POST /api/auth/login` 5/min; 429 with `Retry-After`.
- **F-SEC-3** Security log table of failed logins, failed redeems (IP, time, reason, never the PIN), revocations; visible in the dashboard.
- **F-PRIV-1** No frame is written to disk unless `EVAL_RECORD=1` or per-event snapshots (P1) are enabled by the admin. Frames live in memory only for the duration of processing plus the dashboard fan-out.
- **F-PRIV-2** Privacy mode per camera: dashboards receive `frame_result` JSON only (no binary frames). Event rows never contain images.
- **F-PRIV-3** The phone page shows a persistent notice "This phone is streaming video to <host>" while streaming.

---

## D. Non-functional requirements

| Area | Requirement |
|---|---|
| Security | HTTPS only for all pages and sockets (mkcert locally). Argon2id for the admin password; SHA-256 for tokens, PINs and session ids with a per-record random salt for PINs; constant-time comparison (`hmac.compare_digest`) where comparison is needed; CSRF protection on state-changing admin routes (SameSite=Strict cookie + custom `X-Requested-With` header check); CSP header `default-src 'self'; img-src 'self' blob: data:; connect-src 'self' wss:` applied when FastAPI serves the built frontend (in `make dev` the Vite server serves the SPA and the CSP is not applied, because Vite's React refresh needs inline scripts); no secrets in logs; `.env` git-ignored; `.env.example` committed. |
| Privacy | As F-PRIV-*. Logs contain camera ids and sizes, never frame contents. Retention: events and metrics kept until the admin deletes the session (archive keeps; delete removes). |
| Latency | Design target on an Apple Silicon Mac: server-side receive→dashboard-send p50 ≤ 150 ms and p99 ≤ 400 ms with 3 phones at 10 fps. These are goals for the metrics panel to measure, not acceptance criteria; the acceptance criterion is that the numbers are measured and reported correctly. |
| Throughput | 3 phones at 10 fps (30 frames/s total) processed without unbounded memory; 5 phones degrade gracefully (drops counted, no crash). |
| Reliability | Phone disconnects, server restarts and worker exceptions never lose persisted data; a worker exception marks that camera `error` and processing continues for others; the process must run 8 hours without memory growth beyond 20 % (measured by the load test). |
| Maintainability | Python typed (mypy strict on `publicsafe/core`), ruff + black; TypeScript strict; eslint + prettier; module boundaries in Section E; every public function in `core` documented; ADRs in `docs/adr/` for decisions listed in Section O. |
| Performance | JPEG decode + inference + rules + fan-out for a 1280×720 frame ≤ 60 ms p50 on MPS (measured and reported; not an acceptance gate). CPU fallback must still work at reduced fps. |
| Portability | macOS Apple Silicon primary; Linux x86 with CPU or CUDA must also run tests (CI-ready). Windows not required. |
| Observability | Structured JSON logs (`structlog` or stdlib `logging` with a JSON formatter), request ids, camera ids; `/api/health`, `/api/metrics` (JSON) and `/metrics` (Prometheus text, optional P1). |
| Accessibility | Dashboard keyboard-navigable; colour is never the only status indicator (icons/text as well). |

---

## E. Architecture and data model

### E.1 Components

```
<root>/
  backend/
    publicsafe/
      __init__.py
      config.py            # pydantic-settings; all env vars documented here
      main.py              # FastAPI app factory, static serving, startup checks
      api/                 # HTTP routers: auth, sessions, cameras, zones, pairing, events, metrics, security, test (TEST_MODE only)
      ws/
        camera.py          # /ws/camera endpoint (ingest)
        dashboard.py       # /ws/dashboard endpoint (fan-out)
        hub.py             # subscription registry and throttled fan-out
      core/                # pure, framework-free; must be importable without FastAPI
        geometry.py        # anchors, membership rules, polygon validation, normalisation
        detector.py        # Detector protocol + UltralyticsDetector (+ FakeDetector for tests)
        rules.py           # RuleId enum, compute_all_rules()
        state_machine.py   # ZoneStateMachine with hysteresis
        mailbox.py         # LatestFrameSlot, FifoSlot (for experiments), Scheduler
        timing.py          # span recording, percentile helpers, clock offset estimation
        models.py          # dataclasses: Frame, Detection, FrameResult, ZoneResult, Event
      worker/
        inference.py       # the single inference thread; pulls from Scheduler, runs core, emits results
        pipeline.py        # glue: decode → detect → rules → state machines → persist → hub
      db/
        base.py            # SQLAlchemy engine/session; SQLite WAL pragmas; Postgres via DATABASE_URL
        models.py          # ORM entities (Section K)
        repo.py            # typed repository functions used by api/ and worker/
      migrations/          # Alembic
      eval/
        cli.py             # publicsafe eval ... and publicsafe loadtest ...
        datasets.py        # MOT17 / image-sequence / video readers; zone and GT loaders
        metrics.py         # frame-, person- and event-level metrics; bootstrap; McNemar
        report.py          # Markdown/CSV/JSON report
      cli.py               # typer/click entry point: run, admin set-password, eval, loadtest, certs
    tests/
      unit/ integration/ security/ cv/ load/
    pyproject.toml, requirements.txt (pinned), alembic.ini
  frontend/
    src/
      pages/   Login, Sessions, SessionDetail, CameraDetail, Events, Security, Health, Pair (phone pairing), Camera (phone streaming)
      components/  LiveView (canvas overlay), ZoneEditor, CameraTile, EventTable, MetricsPanel, PinModal
      lib/     api.ts (typed client), ws.ts (dashboard socket), capture.ts (phone capture loop), geometry.ts
    package.json, vite.config.ts (https with the mkcert files, proxy to backend in dev), tsconfig.json
  README.md          # root: launch procedure for macOS
  docs/
    SETUP_MACOS.md, PHONE_SETUP.md (installing the mkcert root CA on iOS/Android; served rendered at /help/phone), API.md, EVALUATION.md, DEMO_CHECKLIST.md, adr/
  configs/         # evaluation configs (e1_mot17.yaml, …) and the zone/GT placeholders referenced by them
  data/            # git-ignored: datasets, recordings, eval outputs
  certs/           # git-ignored: mkcert output
  docker-compose.yml   # optional PostgreSQL only
  Makefile
  .env.example, .gitignore
```

**Rule:** `publicsafe/core` imports only numpy, shapely, opencv-python-headless (decode only) and the standard library. The detector adapter imports ultralytics behind a `Detector` protocol so tests and the evaluation CLI can inject `FakeDetector`.

### E.2 Communication flows

1. **Pairing (HTTP):** dashboard `POST /api/sessions/{id}/pairing-codes` → PIN; phone `POST /api/pair/redeem` → the response defined in F.3.
2. **Ingest (WS):** phone opens `wss://host/ws/camera`; first message `auth`; server `hello`; phone sends binary JPEG frames each preceded by a small JSON `frame_meta` text message (`seq`, `t_phone_ms`, `w`, `h`); server replies `ack` every N frames (N = 10) with `seq`, `t_server_recv_ms`, and counters.
3. **Processing (in-process):** ingest writes into the camera's `LatestFrameSlot`; `Scheduler.next()` round-robins; `InferenceWorker` runs `pipeline.process(frame)` → `FrameResult`; state machines produce events; `repo` persists events and metric samples; `hub.publish(camera_id, FrameResult, jpeg_bytes)`.
4. **Dashboard (WS):** browser opens `wss://host/ws/dashboard` with the admin cookie, sends `subscribe {camera_ids, preview_fps}`; receives `frame_result` JSON (+ binary JPEG tagged by a preceding `frame_preview` text message unless privacy mode), `event`, `camera_status`, `metrics` (every 2 s).
5. **Control (WS):** admin changes settings → `PATCH /api/cameras/{id}` → server sends `settings` to the phone socket and `camera_status` to dashboards.

### E.3 Threads and backpressure

- uvicorn event loop handles all sockets. Frame bytes are handed to the slot without decoding on the loop (decode happens in the worker).
- One `InferenceWorker` thread. It blocks on `Scheduler.next(timeout)` which returns the newest frame of the next camera in round-robin order that has an unread frame. Round-robin guarantees that with K active cameras each gets one turn per round; a camera that produces faster than its share simply has its older frames replaced (counted as `dropped_replaced`).
- `LatestFrameSlot.put(frame)` replaces under a lock; if the previous frame was unread, increment `dropped_replaced`. `take()` returns and clears. Also exposes `FifoSlot(maxlen=None)` used only when `SCHEDULER_STRATEGY=fifo` for experiment E5; it must never be the default.
- Dashboard fan-out is throttled per subscriber to its `preview_fps`; if a dashboard socket's send queue exceeds 5 messages, previews are skipped for it (never `frame_result` JSON or events).
- Hard limits: frame size ≤ `MAX_FRAME_BYTES` (default 1.5 MB; close 1009 if exceeded); frames per second per camera ≤ 2× target (extra frames dropped and counted as `dropped_ratelimit`). Both the 2× drop threshold and the 3×-for-10-s abuse close (F.7) are measured on frames *received*, including those dropped.
- **Development topology:** `make dev` runs uvicorn on `https://0.0.0.0:8443` and Vite on `https://0.0.0.0:5173` with the same certificate, Vite proxying `/api` and `/ws` (WebSocket upgrade enabled) to 8443; the phone uses `https://<lan-ip>:5173/pair` in dev. `make run` serves everything from 8443 and is what demos and the Definition of Done use.

### E.4 Entity relationships

`Session 1—N Camera 1—N Zone`; `Camera 1—1 DeviceCredential (active) 0—N (revoked history)`; `Session 1—N PairingCode`; `Camera 1—N Event (Zone 1—N Event)`; `Camera 1—N MetricSample`; `AdminSession`; `SecurityLog`. Full schema in Section K.

---

## F. API contract

Base URL `https://<host>:8443` (port configurable). JSON bodies; `Content-Type: application/json`. All timestamps ISO-8601 UTC with milliseconds. Errors use one shape:

```json
{"error": {"code": "pin_invalid", "message": "Human readable", "details": {}}}
```

HTTP status mapping: 400 validation (`code: validation_error`, `details.fields`), 401 unauthenticated, 403 forbidden/revoked, 404 not found, 409 conflict, 410 gone (expired PIN/session), 423 locked (PIN attempts exhausted), 429 rate limited (`Retry-After` header), 500 internal (no stack traces to clients).

Authentication: **Admin** = session cookie `ps_admin` + header `X-Requested-With: publicsafe` on non-GET. **Device** = device token, only ever sent inside the WebSocket `auth` message (never as a query parameter or header, so it never appears in access logs).

### F.1 Auth

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | `/api/auth/login` | none (rate-limited 5/min/IP) | `{username, password}` | 204 + cookie; 401 `invalid_credentials` (same message for unknown user and wrong password; constant-time) |
| POST | `/api/auth/logout` | admin | — | 204 |
| GET | `/api/auth/me` | admin | — | `{username, session_expires_at}` |

### F.2 Sessions

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/sessions?include_archived=false` | — | `[{id, name, location_note, created_at, archived_at, camera_count, online_count}]` |
| POST | `/api/sessions` | `{name (1–80 chars), location_note (≤ 200, optional)}` | 201 `{id, …}` |
| GET | `/api/sessions/{id}` | — | session + `cameras: [CameraSummary]` |
| PATCH | `/api/sessions/{id}` | any of `{name, location_note, archived: bool}` | 200 |
| DELETE | `/api/sessions/{id}` | — | 204 after the dashboard confirm dialog; hard-deletes cameras, zones, events, metric samples and pairing codes (DB cascades); revokes tokens; closes sockets with 4003. A pending PIN of a deleted session therefore no longer exists and redeems as 404 `pin_invalid`; an *archived* session's PIN redeems as 410 `session_gone` |

### F.3 Pairing (details in Section G)

| Method | Path | Auth | Request | Response |
|---|---|---|---|---|
| POST | `/api/sessions/{id}/pairing-codes` | admin | `{camera_name_hint?: string, replace_camera_id?: uuid}` (`replace_camera_id` must belong to this session) | 201 `{code_id, pin: "XKD-MPT", expires_at, pair_url: "https://host/pair?c=<code_id>", ttl_seconds}` — the only time the PIN is ever returned |
| GET | `/api/sessions/{id}/pairing-codes` | admin | — | `[{code_id, status: pending|redeemed|expired|locked|cancelled, created_at, expires_at, attempts, redeemed_camera_id}]` (never the PIN). `expired` is computed on read (`status='pending' AND expires_at <= now`); no background sweeper changes code rows |
| DELETE | `/api/pairing-codes/{code_id}` | admin | — | 204 (status → `cancelled`) |
| POST | `/api/pair/redeem` | none; rate-limited | `{pin: string, code_id?: uuid (from the QR/URL), device_label?: string (≤ 60), remember?: bool}` | 200 `{camera_id, camera_name, session_name, device_token, ws_url, settings}`; 404 `pin_invalid` (unknown or wrong, same message); 410 `pin_expired`; 423 `pin_locked`; 409 `pin_already_redeemed`; 410 `session_gone` |
| GET | `/api/pair/precheck` | none | — | `{server_time_ms, version}` (used by the phone page to confirm reachability and measure RTT before pairing) |

### F.4 Cameras

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/cameras?session_id=` | — | `[CameraSummary]` |
| GET | `/api/cameras/{id}` | — | `CameraDetail` = summary + `settings` + `zones` + `health` |
| PATCH | `/api/cameras/{id}` | `{name?, settings?: {target_fps, jpeg_quality, max_long_edge, confidence, privacy_mode}}` | 200; pushes `settings` to the phone |
| POST | `/api/cameras/{id}/revoke` | `{reason?: string}` | 204; credential revoked, socket closed 4003, status `revoked` |
| DELETE | `/api/cameras/{id}` | — | 204 (only when revoked or never connected) |
| GET | `/api/cameras/{id}/last-frame.jpg` | — | latest JPEG (for the zone editor); 404 if none or privacy mode; `Cache-Control: no-store` |

`CameraSummary`: `{id, session_id, name, status, device_label, created_at, last_seen_at, fps_in, fps_processed, drop_ratio, rtt_ms, clock_offset_ms, occupancy: {zone_id: count}, zone_count, needs_review}`.

### F.5 Zones

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/cameras/{id}/zones` | — | `[Zone]` |
| POST | `/api/cameras/{id}/zones` | `{name, points: [[x,y]…] normalised, frame_w, frame_h (the frame size the polygon was drawn on), active_rule, enter_frames, exit_frames, enabled}` | 201 `Zone`; 400 `polygon_invalid` with reason |
| PATCH | `/api/zones/{id}` | any field | 200 |
| DELETE | `/api/zones/{id}` | — | 204 (events keep `zone_id` and the zone row is soft-deleted) |

`Zone`: `{id, camera_id, name, points, frame_w, frame_h, active_rule, enter_frames, exit_frames, enabled, needs_review, created_at, updated_at}`.

Validation: 3 ≤ points ≤ 20; each coordinate in `[0,1]`; `shapely.Polygon(points).is_valid` and `.area > 1e-4`; `enter_frames`, `exit_frames` in 1..60; `active_rule` in the enum.

### F.6 Events, metrics, security, health

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/events` | query `session_id?, camera_id?, zone_id?, type?, from?, to?, limit (≤ 500), cursor?` | `{items: [Event], next_cursor}` |
| GET | `/api/events/export.csv` | same filters | `text/csv` streamed |
| GET | `/api/metrics` | — | `{server: {...}, cameras: {id: {...}}}` current values and percentiles over the last 60 s |
| GET | `/api/metrics/export.csv` | `camera_id?, from?, to?` | CSV of `MetricSample` rows |
| GET | `/api/security/log` | `limit, cursor` | `[SecurityLog]` |
| GET | `/api/health` | none | `{status: "ok", version, uptime_s, device, model_sha256, db: "ok"}` |

`Event`: `{id, session_id, camera_id, zone_id, zone_name, type, t_server, frame_seq, count_before, count_after, active_rule, rule_counts: {<every RuleId in the enum>: n}, created_at}`.

### F.7 WebSocket `/ws/camera` (phone → server)

Connection: `wss://host/ws/camera`. No cookie needed. Text frames are JSON; binary frames are JPEG bytes.

Close codes: `4000` protocol error (bad JSON, unknown type), `4001` auth missing or invalid (also when no `auth` within 5 s), `4003` revoked or session deleted, `4004` superseded by a newer connection for the same camera, `1009` frame too large, `4008` rate limit abuse (> 3× target fps sustained for 10 s), `1011` server error.

| Direction | type | Fields | Notes |
|---|---|---|---|
| phone→server | `auth` | `token`, `client: {ua, w, h, remember}` | must be first message |
| server→phone | `hello` | `camera_id, camera_name, settings: {target_fps, jpeg_quality, max_long_edge, confidence, privacy_mode}, server_time_ms, ack_every: 10` | the phone ignores settings it does not use (`confidence`, `privacy_mode`) |
| phone→server | `frame_meta` | `seq (int, monotonic), t_phone_ms, w, h` | immediately followed by one binary frame; a binary without a preceding meta is a protocol error |
| phone→server | binary | JPEG bytes | |
| server→phone | `ack` | `seq, t_server_recv_ms, processed_seq, dropped_replaced, dropped_ratelimit` | every `ack_every` frames |
| phone→server | `ping` | `t_phone_ms, last_rtt_ms (null on first)` | every 5 s |
| server→phone | `pong` | `t_phone_ms, t_server_ms` | immediately; the phone computes `rtt = now − t_phone_ms` and sends it in the next ping |
| phone→server | `pause` / `resume` | — | status → `paused` / `online` |
| server→phone | `settings` | same as in `hello` | on admin change |
| server→phone | `error` | `code, message` | before a close when useful |

Server-side state per connection: last `seq` (a non-increasing seq is counted as `out_of_order` and the frame is still processed; do not close), frames-in counter, bytes-in, last-seen, and the clock-offset estimator: on each ping that carries `last_rtt_ms`, `offset_sample = t_server_recv_ms − (t_phone_ms + last_rtt_ms/2)`; keep an exponential moving average (α = 0.2) and the latest RTT; both are reported in metrics. The estimate is used only for the `e2e_estimate_ms` metric, never for ordering or event timestamps.

### F.8 WebSocket `/ws/dashboard` (server → browser)

Connection requires the admin cookie (`websocket.cookies`). On failure the server **accepts the socket and then closes it with 4001** so the browser can read the code (closing before `accept()` surfaces as an HTTP 403 and the browser only sees 1006).

| Direction | type | Fields |
|---|---|---|
| browser→server | `subscribe` | `camera_ids: [..] or "all", preview_fps (1–30)`; `"all"` includes cameras created after subscribing |
| browser→server | `unsubscribe` | `camera_ids` |
| server→browser | `frame_result` | `camera_id, seq, t_server_recv_ms, t_processed_ms, w, h, detections: [{bbox: [x1,y1,x2,y2] (pixels), conf, anchors: {bl: [x,y], bc: [x,y], br: [x,y]}, track_id?}], zones: [{zone_id, rule_hits: {rule: [det_index…]}, counts: {rule: n}, active_rule, state: clear|occupied, debounced_count}], timings: {decode_ms, infer_ms, rules_ms, total_ms}` |
| server→browser | `frame_preview` | `camera_id, seq, bytes` — followed by one binary JPEG; omitted in privacy mode |
| server→browser | `event` | the `Event` object |
| server→browser | `camera_status` | `camera_id, status, last_seen_at, reason?` |
| server→browser | `metrics` | same payload as `GET /api/metrics`, every 2 s |
| server→browser | `error` | `code, message` |

---

## G. PIN registration protocol

### G.1 Code generation

- Alphabet: `BCDFGHJKLMNPQRSTVWXZ` (20 symbols, RFC 8628 §6.1 recommendation: no vowels, no 0/O/1/I confusion). Length 6 (≈ 25.9 bits), displayed in two groups of three, e.g. `XKD-MPT`. Setting `PIN_ALPHABET=letters|digits`; `digits` uses 6 decimal digits (≈ 19.9 bits) and is acceptable only because of the attempt limits below. Input normalisation: uppercase, strip spaces and hyphens; any character outside the alphabet (vowels, `0`, `O`, `1`, `I`) makes the input invalid immediately, so typos fail fast without a database lookup or an attempt being counted.
- Generation uses `secrets.choice`. Uniqueness: the server stores `pin_hash = sha256(salt || pin)` with a 16-byte random `salt` per code; before issuing, it checks that no *pending* code in the same server has the same PIN (loop until unique; collision probability is negligible but the check keeps redemption unambiguous).
- Lifetime `PIN_TTL_SECONDS` default 300. `max_attempts` default 5.
- The plaintext PIN is returned once in the minting response and shown on screen; it is never logged, never returned by any list endpoint, never stored in plaintext.

### G.2 Redemption (atomic)

On `POST /api/pair/redeem`:

1. Rate limit by IP (10/min, 30/10 min). Exceeded → 429.
2. Normalise input; if it contains characters outside the alphabet → 404 `pin_invalid` (no DB access, no attempt counted).
3. Candidate codes: if `code_id` is supplied, the single code with that id (404 `pin_invalid` if it does not exist); otherwise all `pending` codes with `expires_at > now` (there are few). For each candidate compute `sha256(salt || pin)` and compare with `hmac.compare_digest`. (Without `code_id` this is O(pending codes), typically < 10, and avoids a lookup keyed by PIN.)
4. No match → if `code_id` was supplied: `attempts += 1` on that code; when `attempts >= max_attempts` set `status='locked'`, log `pin_locked`, respond 423 `pin_locked`; otherwise respond 404 `pin_invalid`. If `code_id` was not supplied: no code counter changes (an attacker learns nothing about which code they were near); respond 404 `pin_invalid`. In both cases log `SecurityLog(kind=redeem_failed, ip)` and count the failure in the per-IP limiter. (Anyone holding a QR URL can lock that code deliberately; this is accepted, visible in the security log, and costs the admin one click to mint a new code.)
5. Match found → in one transaction: `UPDATE pairing_codes SET status='redeemed', redeemed_at=now, redeemed_ip=? WHERE id=? AND status='pending' AND expires_at > now`. If `rowcount != 1`, re-read the row: `status='redeemed'` → 409 `pin_already_redeemed`; `status='locked'` → 423; `status='cancelled'` → 404; expired → 410 `pin_expired`. If the session is archived → set the code `cancelled`, respond 410 `session_gone`. Otherwise insert `Camera(status=paired)` (or, when the code carries `replace_camera_id`, revoke that camera's active credential with reason `replaced`, close its socket with 4003, set its status to `paired`) and `DeviceCredential(token_hash, camera_id, created_ip)` in the same transaction; commit.
6. Response includes `device_token` (plaintext, once), `camera_id`, `ws_url`, and settings.

Guess bound without `code_id`: the IP limiter allows 30 attempts per 10 minutes, so within a 5-minute PIN lifetime an attacker gets at most 30 guesses per IP against 20⁶ ≈ 64 million codes (letters) or 10⁶ (digits).

### G.3 Device credentials

- Token: `secrets.token_urlsafe(32)` (256 bits). Stored as `sha256(token)` (no salt needed at this entropy). Bound to exactly one camera. Never expires by time (the phone is a fixed sensor), but is revoked by admin action, by session deletion, or by re-pairing the camera.
- Authentication on `/ws/camera`: the server accepts the socket, then requires an `auth` message within 5 s; it hashes the token and looks up the credential. Unknown hash, malformed message or timeout → close 4001. Credential revoked, camera revoked, or session archived/deleted → close 4003. On success update `last_used_at`.
- Password rotation: `publicsafe admin set-password` deletes all `admin_sessions` rows after writing the new hash; device credentials are unaffected.
- One live connection per camera: a new successful `auth` for a camera that already has a connection closes the old one with 4004 and takes over (handles a phone that reloads).
- Revocation: `POST /api/cameras/{id}/revoke` sets `revoked_at`, `revoked_reason`; the server closes the socket (4003) within 1 s; the phone deletes its stored token on 4003.
- Re-pairing an existing camera (admin "Replace phone") mints a new code with `replace_camera_id`; redemption revokes the old credential and attaches the new one to the same camera id so zones and history persist.

### G.4 Failure handling summary

| Situation | Behaviour |
|---|---|
| PIN typed wrong | 404 generic; counter on the code only if `code_id` known; IP limiter always |
| PIN expired | 410; dashboard shows "Expired, mint a new one" |
| Session deleted after minting | code row is gone → 404 `pin_invalid`; session archived → 410 `session_gone` |
| Two phones redeem simultaneously | exactly one wins (rowcount check); the other gets 409 |
| Token lost on phone | admin mints a new code with "Replace phone" |
| Token leaked | admin revokes; any connection using it closes 4003; security log entry |
| Server restarted | credentials and codes are in the DB; pending codes keep their expiry |
| Clock skew between phone and server | irrelevant to pairing (server time only) |

---

## H. Computer-vision pipeline

### H.1 Frame intake

- Decode JPEG with `cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)`; reject `None` as `decode_error` (counted, not fatal). Verify `(w, h)` against `frame_meta`; use the decoded size as truth.
- Resize only if the long edge exceeds the camera's `max_long_edge` (the phone should already comply).
- `Frame` dataclass: `camera_id, seq, t_phone_ms, t_server_recv_ms, t_dequeued_ms, w, h, image (ndarray BGR), jpeg (bytes)`.

### H.2 Detection

- `Detector` protocol: `detect(image: ndarray) -> list[Detection]` with `Detection(bbox_xyxy: tuple[float,float,float,float], conf: float, cls: int = 0, track_id: int | None = None)`.
- `UltralyticsDetector(model_path="yolov8n.pt", device, conf, imgsz=640, half=False)`, calling `model.predict(image, conf=conf, classes=[0], device=device, imgsz=imgsz, half=half, verbose=False)`. `half` is a setting (`INFERENCE_HALF`, default false); the agent must measure whether FP16 on MPS is faster and correct before recommending it. One model instance shared by the single worker thread. Warm-up with one dummy frame at startup; record `model_sha256`.
- Record `infer_ms`. On exception: log, mark camera `error` for this frame, continue.
- (P1) Tracking, if enabled, uses the `trackers` package (Apache-2.0; its ByteTrack class name must be verified against the installed version) with one tracker instance per camera on top of detections, or `model.track(..., persist=True, tracker="bytetrack.yaml")` **with one model instance per camera** (Ultralytics keeps tracker state per model). Tracking must be optional and off by default.

### H.3 Membership rules (all computed every frame)

Anchors from `bbox = (x1, y1, x2, y2)` in pixels: `bl = (x1, y2)`, `bc = ((x1+x2)/2, y2)`, `br = (x2, y2)`.

Zone polygon in pixels: `points_px = [(x·w, y·h) for (x, y) in points_norm]` using the *current frame's* `w, h`. Build `shapely.Polygon`, `shapely.prepare()` once per (zone, w, h) and cache.

| RuleId | Positive when |
|---|---|
| `bbox_overlap` | `polygon.intersects(box(x1, y1, x2, y2))` and intersection area > 0 |
| `bottom_center` | `polygon.covers(Point(bc))` (boundary counts as inside) |
| `three_point_any` | any of `bl, bc, br` covered |
| `three_point_majority` | ≥ 2 of 3 covered |
| `three_point_all` | all 3 covered |
| `bottom_strip` | intersection area of `box(x1, y2 − 0.1·(y2−y1), x2, y2)` with the polygon ≥ 0.5 × strip area. Part of the enum from the start; `rule_counts`, CSV columns and the eval report always include it |

Invariants (unit-tested with random boxes and polygons): `all ⊆ majority ⊆ any ⊆ bbox_overlap`; `bottom_center ⊆ any`. Boundary semantics: use `covers`, and keep a cross-check test that `cv2.pointPolygonTest(contour, pt, False) >= 0` agrees with `covers` for 1,000 random points (allowing disagreement only within 1e-6 of an edge).

Output per zone: `rule_hits: dict[RuleId, list[int]]` (detection indices), `counts: dict[RuleId, int]`.

### H.4 Zone state machine

```
state: clear | occupied; counters consecutive_in, consecutive_out, consecutive_change, candidate_count, debounced_count
raw_count = counts[active_rule]
if state == clear:
    consecutive_in = consecutive_in + 1 if raw_count >= 1 else 0
    if consecutive_in >= enter_frames:
        state = occupied; debounced_count = raw_count; consecutive_out = 0; consecutive_change = 0
        emit zone_entered(count_before=0, count_after=raw_count)
        return   # the entering frame does not also run the occupied block
if state == occupied:
    consecutive_out = consecutive_out + 1 if raw_count == 0 else 0
    if consecutive_out >= exit_frames:
        state = clear; emit zone_cleared(count_before=debounced_count, count_after=0)
        debounced_count = 0; consecutive_in = 0; consecutive_change = 0
        return
    if raw_count >= 1 and raw_count != debounced_count:
        if raw_count == candidate_count: consecutive_change += 1 else: candidate_count = raw_count; consecutive_change = 1
        if consecutive_change >= enter_frames:
            emit occupancy_changed(count_before=debounced_count, count_after=raw_count); debounced_count = raw_count; consecutive_change = 0
    else:
        consecutive_change = 0
```

- `t` passed to `step(counts, t_ms)` is `t_server_recv_ms` of the frame (eval: the frame index converted with the sequence fps). Frame gaps: if `t − t_last > 2000 ms`, reset all consecutive counters but keep `state` and `debounced_count` (a disconnect does not fabricate a `zone_cleared`). On camera `revoked` or session delete, emit nothing. `enabled=false`: `step` returns `[]` and changes nothing.
- Deterministic and pure: `ZoneStateMachine.step(counts, t_ms) -> list[Event]`; fully unit-testable.
- Same class is used in the evaluation CLI, applied to ground truth and to predictions identically.

### H.5 Timing instrumentation

Per frame: `t_server_recv_ms` (loop), `t_dequeued_ms` (worker), `t_decoded_ms`, `t_inferred_ms`, `t_rules_ms`, `t_published_ms`. Spans: `queue_wait = dequeued − recv`, `decode`, `infer`, `rules`, `publish`, `total = published − recv`. Keep a ring buffer of the last 2,000 totals per camera; compute p50/p90/p95/p99 on request. `MetricSample` rows are written every 5 s per camera with the window's fps in/processed, drops (replaced, rate-limited), decode errors, out-of-order count, p50/p95/p99 total, infer p50/p95, queue-wait p50, RTT, offset. End-to-end estimate = `total + (recv − (t_phone_ms + offset))` reported separately as `e2e_estimate_ms` with the offset's RTT uncertainty.

### H.6 Scheduler strategies

`SCHEDULER_STRATEGY=latest` (default): per-camera `LatestFrameSlot` + round-robin. `fifo`: a single shared unbounded FIFO across cameras (the paper's baseline) — **experiments only**, printed as a warning at startup. The load test must be able to run both.

### H.7 Evaluation mode

`EVAL_RECORD=1` writes, per processed frame, `<seq>.jpg` (the bytes as received, not re-encoded) and appends a JSON line with the full `FrameResult` and timings to `frames.jsonl`. The evaluation CLI can replay such a directory as an image sequence with `t_phone_ms` preserved.

---

## I. Dataset and experiment plan

### I.1 Datasets (verified 2026-10-10; the agent must re-check licences before downloading)

| Dataset | Use | Source | Licence / access | Notes |
|---|---|---|---|---|
| MOT17 (train sequences 02, 04, 09) | **Primary** for rule comparison and event metrics | https://motchallenge.net/data/MOT17/ | CC BY-NC-SA 3.0 (stated on https://motchallenge.net/); direct download, ~5.5 GB for all data | 1920×1080, 25–30 fps, full-body boxes with track ids in `gt/gt.txt`. The ground-truth files use 9 comma-separated columns `frame, id, left, top, w, h, flag, class, visibility` (1-based frame and id; `flag=0` = ignore; `class` 1 = pedestrian, with other classes for riders, static persons, distractors and occluders; `visibility` 0–1). **The agent must confirm the column meaning and the class map against the README/devkit shipped in the download before writing the reader**, and must expose `--gt-classes` (default `1`) and `--min-visibility` (default 0.25) as eval options, because these choices affect the metrics. MOT17-04 is "pedestrian street at night, elevated viewpoint". No zone ground truth: zones are drawn by the team and committed as JSON. |
| WILDTRACK | **Backup / geometric check** | https://www.epfl.ch/labs/cvlab/data/data-wildtrack/ | Licence not stated on the page — confirm with EPFL CVLab before publishing results | 7 static 1080p cameras, 400 annotated frames per view at 2 fps, per-view boxes plus a ground-plane grid cell per person (480×1440 grid, 2.5 cm) and OpenCV calibration; toolkit https://github.com/Chavdarova/WILDTRACK-toolkit (GPL-3.0 code). Allows a metric zone with truth independent of box heuristics. |
| Own recordings | Required for live experiments and conditions the public data lack | recorded with `EVAL_RECORD=1` | internal; obtain consent of people filmed; keep private | Paper's four scenarios + night, occlusion, tangential approach. |
| Not used | CityPersons, EuroCity Persons, BDD100K, JAAD, PIE, CrowdHuman, Caltech | — | — | Ego-vehicle or still-image detection sets; no fixed zone is definable. Oxford Town Centre (no longer distributed), PETS2009 (host unreachable), DukeMTMC (withdrawn 2019) must not be used. |

`publicsafe eval fetch mot17 --dest data/mot17` downloads and verifies by SHA-256 (the agent must obtain the archive's checksum by downloading once and recording it; never invent one). The agent must not commit dataset files.

### I.2 Zone files

`data/zones/<sequence>.json`:

```json
{"sequence": "MOT17-04", "frame_w": 1920, "frame_h": 1080,
 "zones": [{"name": "strip", "points": [[0.10,0.55],[0.90,0.55],[0.90,0.75],[0.10,0.75]]},
           {"name": "corner", "points": [[0.60,0.30],[0.95,0.30],[0.95,0.60]]},
           {"name": "irregular", "points": [[0.2,0.4],[0.5,0.35],[0.7,0.6],[0.4,0.8],[0.15,0.7]]}]}
```

The three polygons per sequence are chosen by the owner, not the agent (the agent creates placeholders and a `TODO` list; the owner replaces them).

### I.3 Ground truth

`data/gt/<sequence>.<zone>.csv` with columns `frame, person_id, in_zone (0/1), source (manual|groundplane|derived), annotator`. Tooling: `publicsafe eval annotate --sequence … --zone … --every 5 --annotator <name>` starts a small local web page on `http://127.0.0.1:<port>` (a secure context, no certificate needed) that shows each k-th frame with its GT boxes and the polygon, lets the annotator toggle `in_zone` per person with keyboard shortcuts, and appends to the CSV; it reuses the frontend's canvas overlay code but is served by the CLI and needs no login. Two annotators → `publicsafe eval agreement` prints Cohen's κ and disagreement list. `source=derived` (GT bottom-centre inside polygon) may be produced automatically but is clearly labelled and **must not** be used for the headline comparison, only for sanity checks.

For WILDTRACK: `in_zone` from the ground-plane cell centre inside a metric polygon; zone defined in `data/zones/wildtrack.<view>.json` with both `points_world_m` and the projected `points` per view.

### I.4 Matching and metrics (`publicsafe/eval/metrics.py`)

- Detection→GT matching per frame: Hungarian on IoU with threshold 0.5. GT rows with `flag=0`, a class outside `--gt-classes`, or visibility below `--min-visibility` are "ignore" regions: they are not counted as GT and any detection whose best IoU is with an ignore region (≥ 0.5) is discarded rather than counted as FP.
- Per-person membership: for each matched pair and each rule: TP if GT `in_zone=1` and rule positive; FP if GT 0 and rule positive; FN if GT 1 and rule negative; TN otherwise. Unmatched detection with rule positive → FP. Unmatched GT with `in_zone=1` → FN (reported separately as `fn_missed_detection` so detector misses are distinguishable from rule errors).
- Frame-level occupancy per zone: `pred = counts[rule] ≥ 1`, `truth = any GT in_zone`.
- Event-level: apply `ZoneStateMachine(enter_frames, exit_frames)` to the per-frame truth and to each rule's per-frame counts; match `zone_entered` events within ±`tolerance_frames` (default 10) greedily by nearest time; precision, recall, F1, timing error median/p90, duplicate rate (predicted events with no truth within tolerance while a truth event is already matched).
- Statistics: bootstrap (5,000 resamples over frames, seed 1234) 95 % CIs for precision, recall, F1; McNemar exact test on paired per-person outcomes for each pair of rules; boundary analysis: for each GT person, distance of the GT bottom-centre to the polygon boundary (signed, px), binned by 10 px, error rate per bin per rule.
- Detector recall at IoU 0.5 per sequence.

### I.5 Experiments (pre-registered; the agent implements the harness, the owner runs and interprets)

| Id | Question | Command | Output |
|---|---|---|---|
| E1 | Foot-anchored rules vs bbox overlap | `eval run --config configs/e1_mot17.yaml` | per-rule P/R/F1 + CIs, McNemar matrix |
| E2 | Which 3-point combination; invariant check | same run | rule table, invariants asserted (`assert_invariants=true` fails the run if violated) |
| E3 | Hysteresis effect on duplicates | `eval run --sweep enter_frames=1,3,5 exit_frames=1,5,10` | event metrics per setting |
| E4 | Tracking (P1) | `eval run --track` | event metrics with/without |
| E5 | Latest vs FIFO under load | `loadtest --phones 1,2,3,5 --queue latest` and `--queue fifo` | per-camera fps, drop ratio, span percentiles, fairness ratio |
| E6 | Live latency with real phones | metrics export during a 5-minute session per phone count | p50/p90/p95/p99 spans, e2e estimate with offset uncertainty |
| E7 | Difficult conditions | `eval run` on own recordings per condition | per-condition table |

Pre-registered success criteria (copy verbatim into `docs/EVALUATION.md`; the agent must not alter them):

| Exp | Success criterion |
|---|---|
| E1 | At least one of `bottom_center` / `three_point_*` has precision ≥ `bbox_overlap` + 0.10 with non-overlapping 95 % CIs and recall loss ≤ 0.05, on ≥ 2 of the 3 data sources (MOT17, WILDTRACK, own recordings) |
| E2 | All rule results reported; invariants `all ⊆ majority ⊆ any ⊆ bbox_overlap` and `bottom_center ⊆ any` hold on every sequence; the active rule is recommended by F1 with the boundary analysis shown |
| E3 | Duplicate-event rate falls by ≥ 50 % with hysteresis (3 in / 5 out) vs none, with event timing error p90 ≤ 10 frames |
| E4 | Event F1 with tracking improves or is unchanged; reported either way |
| E5 | With 3 cameras, p99 queue-wait latency under `latest` is ≥ 30 % lower than under `fifo`; fairness ratio (max ÷ min processed fps) ≤ 1.3 under `latest` |
| E6 | Server-side receive→send p50 reported; end-to-end estimate reported with the RTT uncertainty; ≤ 200 ms p50 is a goal, not a pass/fail gate |
| E7 | Per-condition recall and FPR tabulated; descriptive only |

### I.5a Config and output formats

- `configs/e1_mot17.yaml` keys: `sequences: [{name, path, fps, zone_file, gt_files: {zone_name: path}}]`, `detector: {model, device, conf, imgsz, half}`, `rules: [list or "all"]`, `state_machine: {enter_frames, exit_frames}`, `matching: {iou: 0.5, gt_classes: [1], min_visibility: 0.25}`, `events: {tolerance_frames: 10}`, `stats: {bootstrap_n: 5000, seed: 1234}`, `assert_invariants: true`, `output_dir`.
- `run.json`: `{run_id, started_at, finished_at, host, platform, python, device, package_versions, model_sha256, git_commit|null, config_sha256}`.
- `per_frame.jsonl`: one line per frame holding the live server's `frame_result` payload (F.8: `detections` with `anchors`, `zones` list with `rule_hits`, `counts`, `active_rule`, `state`, `debounced_count`) extended with `sequence`, `matched_gt_id` per detection and `gt: [{person_id, bbox, in_zone}]`.
- `results.csv`: one row per (sequence, zone, rule) with `tp, fp, fn, tn, fn_missed_detection, precision, recall, f1, fpr, precision_ci_lo, precision_ci_hi, recall_ci_lo, recall_ci_hi, f1_ci_lo, f1_ci_hi, event_precision, event_recall, event_f1, timing_err_median, timing_err_p90, duplicate_rate, detector_recall`.
- `metrics.json`: the same plus the McNemar matrix and boundary bins.

### I.6 Reproducibility requirements

- `requirements.txt` fully pinned (generated with `pip freeze` after install); `yolov8n.pt` SHA-256 written to `docs/EVALUATION.md` after first download; `torch.manual_seed`/`numpy` seeds set in eval; config YAML copied into every output directory with a `run.json` (timestamp, host, device, package versions, git commit if available, config hash).
- Output directory layout: `data/eval/<run_id>/{config.yaml, run.json, per_frame.jsonl, results.csv, metrics.json, report.md, figures/}`.
- `report.md` contains the tables from I.4 and, in `figures/`, precision-recall bars per rule, boundary-distance error curves, event timing histogram, latency percentile table (for E5/E6). Use matplotlib; no interactive dependencies.

---

## J. Frontend requirements

Vite + React 19 + TypeScript (strict). State: React Query (TanStack) for HTTP, a small WebSocket store (zustand or context) for live data. Styling: any single approach (CSS modules or Tailwind); dark and light theme following `prefers-color-scheme`. All pages responsive down to 360 px width; the phone page is designed for portrait phones first.

### J.1 Pages

| Route | Page | Content and interactions |
|---|---|---|
| `/login` | Login | username/password; inline error "Invalid credentials"; rate-limit message with countdown on 429 |
| `/` | Sessions | list with online/total cameras; "New session" dialog; archived toggle; empty state "No sessions yet — create one to start pairing phones" |
| `/sessions/:id` | Session detail | header with name/location; **camera grid** of `CameraTile` (live thumbnail at ≤ 5 fps, status badge, occupancy, fps/drop); "Add camera" → `PinModal`; events panel (last 50, live); metrics panel; archive/delete |
| `/cameras/:id` | Camera detail | large `LiveView` (canvas; JPEG drawn then overlays: boxes, anchors, polygon, counts); `ZoneEditor` toggle; settings form (fps, quality, max edge, confidence, privacy); health card; revoke/replace phone buttons with confirm dialogs |
| `/events` | Events | filter bar (session, camera, type, from/to); virtualised table; CSV export of current filter; row click shows the per-rule counts for that frame |
| `/security` | Security | failed logins/redeems, revocations; refresh |
| `/health` | Health | server version, device, model hash, uptime, DB, recording flag |
| `/pair` | Phone pairing (public) | precheck (secure context, mediaDevices, server reachable); PIN input (6 boxes, auto-advance, paste support, uppercase); "remember this phone"; errors: invalid, expired, locked, rate-limited (with retry time), session gone; on success → `/camera` |
| `/camera` | Phone streaming (public, needs stored token) | preview, status line (Pairing → Requesting camera → Connecting → Streaming → Reconnecting (n) → Paused → Stopped: reason), send fps, last ack, "Forget this phone", privacy notice; big simple layout; prevents sleep via Wake Lock when available and tells the user to keep the screen on otherwise |

### J.2 `PinModal`

Shows the PIN in large monospace groups, a countdown to expiry, the pairing URL and a QR code (`qrcode` npm package) encoding `https://host/pair?c=<code_id>`; "Copy", "Regenerate", "Cancel code". On redemption (via `camera_status` message) it switches to "Phone connected: <name>" with a link to the camera.

### J.3 `ZoneEditor`

- Draws over the current frame (from `last-frame.jpg` refreshed every 2 s, or the live canvas) with the same pixel→normalised mapping as the server (`x/w`, `y/h` on the *frame*, not the CSS size).
- Interactions per F-ZONE-1. Shows vertex count, validation errors inline (self-intersection, < 3 points), rule selector with one-line descriptions, enter/exit frame inputs, Save/Cancel. Undo (Ctrl/Cmd-Z) for the last vertex.
- When `needs_review` is set, show a banner "Frame size changed since this zone was drawn; verify it."

### J.4 States

Every data view implements: loading (skeleton), empty (one sentence + primary action), error (message + retry), and stale (WebSocket disconnected → yellow banner "Live updates paused, reconnecting…" with automatic reconnect using the same backoff policy as the phone).

Camera permission handling on the phone page: `NotAllowedError` → instructions per platform (iOS: Settings › Safari › Camera; Android Chrome: site settings); `NotFoundError` → "No camera found"; `OverconstrainedError` (only possible if `exact` constraints are ever used; the default constraints use `ideal`) → retry with `ideal`; `NotReadableError` → "Camera is used by another app".

Unsupported browser: if `isSecureContext` is false → show the https URL to use and a link to `docs/PHONE_SETUP.md` (served at `/help/phone`); if `mediaDevices` missing → "This browser cannot access the camera; use Safari (iOS) or Chrome (Android)".

### J.5 Accessibility and responsiveness

Focus management in dialogs, labels on inputs, status conveyed by text + icon + colour, minimum 44 px touch targets on the phone page, camera grid collapses to one column under 700 px.

---

## K. Database schema

SQLAlchemy 2.x declarative models, Alembic migrations from the first commit (`alembic revision --autogenerate` is acceptable but each migration must be reviewed and named). SQLite by default (`sqlite+pysqlite:///data/publicsafe.db`, pragmas `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`); PostgreSQL via `DATABASE_URL=postgresql+psycopg://…`. Use `sqlalchemy.JSON().with_variant(postgresql.JSONB(), "postgresql")` for the fields marked JSON (the generic `JSON` type alone maps to plain `json` on PostgreSQL). All ids are UUID4 stored as `CHAR(36)`/`UUID`. All timestamps UTC `DATETIME(timezone=True)`.

| Table | Columns (type, constraints) | Indexes |
|---|---|---|
| `sessions` | `id PK`, `name VARCHAR(80) NOT NULL`, `location_note VARCHAR(200)`, `created_at`, `archived_at NULL` | `(archived_at)` |
| `cameras` | `id PK`, `session_id FK sessions ON DELETE CASCADE`, `name VARCHAR(80)`, `device_label VARCHAR(60)`, `status VARCHAR(16) CHECK IN (paired, online, offline, paused, error, revoked)`, `settings JSON NOT NULL` (`{target_fps, jpeg_quality, max_long_edge, confidence, privacy_mode}`), `last_seen_at NULL`, `last_frame_w INT NULL`, `last_frame_h INT NULL`, `created_at`, `revoked_at NULL` | `(session_id)`, `(status)` |
| `device_credentials` | `id PK`, `camera_id FK cameras CASCADE`, `token_hash CHAR(64) UNIQUE NOT NULL`, `created_at`, `created_ip VARCHAR(45)`, `last_used_at`, `revoked_at NULL`, `revoked_reason VARCHAR(120)` | `(token_hash)`, `(camera_id, revoked_at)` |
| `pairing_codes` | `id PK`, `session_id FK CASCADE`, `pin_hash CHAR(64) NOT NULL`, `salt CHAR(32) NOT NULL`, `status VARCHAR(12) CHECK IN (pending, redeemed, locked, cancelled)` (`expired` is derived on read), `attempts INT DEFAULT 0`, `max_attempts INT DEFAULT 5`, `expires_at NOT NULL`, `created_at`, `redeemed_at NULL`, `redeemed_ip VARCHAR(45) NULL`, `redeemed_camera_id FK cameras ON DELETE SET NULL`, `replace_camera_id FK cameras ON DELETE SET NULL`, `camera_name_hint VARCHAR(80)` | `(status, expires_at)` |
| `zones` | `id PK`, `camera_id FK CASCADE`, `name VARCHAR(80)`, `points JSON NOT NULL`, `frame_w INT NOT NULL`, `frame_h INT NOT NULL` (required in POST; the editor supplies the frame size it drew on), `active_rule VARCHAR(24) NOT NULL`, `enter_frames INT DEFAULT 3 CHECK 1..60`, `exit_frames INT DEFAULT 5 CHECK 1..60`, `enabled BOOL DEFAULT TRUE`, `needs_review BOOL DEFAULT FALSE`, `deleted_at NULL`, `created_at`, `updated_at` | `(camera_id, deleted_at)` |
| `events` | `id PK`, `session_id FK sessions ON DELETE CASCADE`, `camera_id FK cameras ON DELETE CASCADE`, `zone_id FK zones ON DELETE SET NULL` (zones are soft-deleted in normal operation), `zone_name VARCHAR(80)` (snapshot for CSV), `type VARCHAR(20) CHECK IN (zone_entered, zone_cleared, occupancy_changed)`, `t_server DATETIME NOT NULL`, `frame_seq BIGINT`, `count_before INT`, `count_after INT`, `active_rule VARCHAR(24)`, `rule_counts JSON`, `created_at` | `(camera_id, t_server)`, `(session_id, t_server)`, `(type)` |
| `metric_samples` | `id PK`, `camera_id FK CASCADE`, `t DATETIME`, `window_s REAL`, `fps_in REAL`, `fps_processed REAL`, `dropped_replaced INT`, `dropped_ratelimit INT`, `decode_errors INT`, `out_of_order INT`, `total_p50 REAL`, `total_p95 REAL`, `total_p99 REAL`, `infer_p50 REAL`, `infer_p95 REAL`, `queue_wait_p50 REAL`, `rtt_ms REAL`, `clock_offset_ms REAL`, `e2e_estimate_p50 REAL`, `server_cpu REAL`, `server_mem_mb REAL` | `(camera_id, t)` |
| `admin_sessions` | `id_hash CHAR(64) PK`, `created_at`, `expires_at`, `last_seen_at`, `ip` | `(expires_at)` |
| `security_log` | `id PK`, `t`, `kind VARCHAR(24)` (`login_failed, login_ok, redeem_failed, redeem_ok, pin_locked, token_revoked, ws_auth_failed, ws_superseded`), `ip VARCHAR(45)`, `camera_id NULL`, `session_id NULL`, `details JSON` (never contains PINs or tokens) | `(t)`, `(kind, t)` |

CSV export columns — events: `id, session_id, session_name, camera_id, camera_name, zone_id, zone_name, type, t_server, frame_seq, count_before, count_after, active_rule` followed by one column per `RuleId` in enum order (`bbox_overlap, bottom_center, three_point_any, three_point_majority, three_point_all, bottom_strip`). Metrics: all `metric_samples` columns plus `camera_name`.

Migration strategy: `alembic upgrade head` runs automatically at server start when `AUTO_MIGRATE=true` (default true for local dev); otherwise the server refuses to start on a schema mismatch and prints the command. Every migration must have a hand-written, tested `downgrade()`. Deleting a session hard-deletes its rows (events included) after a confirm dialog that states the count.

**TEST_MODE** (`TEST_MODE=1`, printed as a startup warning, refused when `APP_ENV=production`): enables `POST /api/test/cameras` (admin; creates a camera in a session and returns a device token without a PIN) and `POST /api/test/detector` (admin; body `{script: [[{bbox, conf}, …] per frame]}` which makes the `FakeDetector` return the scripted detections in order, cycling). Used by Playwright and the fake-phone tests only; the load test uses the real pairing flow.

---

## L. Testing requirements

Test commands: `make test` (unit + integration + security + cv, must pass with no network and no GPU, using `FakeDetector`), `make test-browser` (Playwright), `make test-load` (load test, prints a table, asserts no crash and fairness bound), `make test-model` (real YOLOv8n on a golden image; needs the weights).

| Level | What | Tooling |
|---|---|---|
| Unit — geometry | anchors; each rule on hand-built cases: box fully inside, outside, straddling top edge (upper body in, feet out → only `bbox_overlap` positive), feet on the edge (`covers` true), degenerate polygons rejected; property test of invariants with 1,000 random boxes/polygons (hypothesis); `pointPolygonTest` cross-check | pytest, hypothesis |
| Unit — state machine | sequences: 1-frame blip → no event; 3 frames → `zone_entered`; flicker 1-0-1-0 → still occupied until `exit_frames`; count change → `occupancy_changed` once; gap reset; identical results for GT and prediction paths | pytest |
| Unit — mailbox/scheduler | 3 producers at 30 fps, consumer at 20 fps → each camera gets ≥ 6 fps and `dropped_replaced` counted; no frame of camera A is ever returned under camera B's id; FIFO strategy grows unbounded in a synthetic test (documents why it is not default) | pytest, threads |
| Unit — pairing | PIN normalisation, alphabet rejection, hashing, expiry, locking at `max_attempts`, single-use (rowcount), concurrency (two threads redeem, one wins), `replace_camera_id` path | pytest with SQLite in-memory + a threading test on a file DB |
| Integration — API | every endpoint happy path and validation errors with `httpx.AsyncClient`; cookie auth; CSRF header; CSV export shape | pytest-asyncio |
| Integration — WebSocket | camera socket: auth timeout 4001, bad token 4001, revoked 4003, superseded 4004, frame too large 1009, meta/binary ordering 4000, ack cadence, ping/pong offset estimation with a simulated 40 ms skew; dashboard socket: subscribe, receives `frame_result` and `event`, privacy mode suppresses binary | starlette `TestClient.websocket_connect` + a `FakeDetector` returning scripted boxes |
| Security | rate limits return 429 with `Retry-After`; login timing for unknown vs wrong password within 20 % of each other over 50 runs; tokens never appear in logs (grep the captured log); PIN never in any response except mint; secrets not in repo (`detect-secrets` or a simple grep in CI); CSP header present; cookie flags | pytest |
| CV | golden image test: `tests/cv/fixtures/person_in_zone.jpg` with real YOLOv8n → ≥ 1 box with conf ≥ 0.3 and bottom-centre inside the fixture zone; `FakeDetector` pipeline end-to-end produces expected events from a scripted 60-frame sequence; eval metrics on a 100-frame synthetic sequence equal hand-computed values; eval invariants assertion fails on a deliberately corrupted results file | pytest (`-m model` for the real-model test) |
| Browser | Playwright: login → create session → mint PIN → open `/pair` in a second context with `--use-fake-device-for-media-stream` and `--use-fake-ui-for-media-stream` (Chromium) → pair → `/camera` reaches "Streaming" → dashboard tile turns online → draw a polygon → an event appears when `FakeDetector` is scripted via a test-only endpoint (`TEST_MODE=1`) | Playwright (Chromium); manual checklist for iOS Safari and Android Chrome in `docs/PHONE_SETUP.md` |
| Load | `make test-load` starts the server with `TEST_MODE=1` and `FakeDetector(sleep=20ms)`, runs `publicsafe loadtest --phones 3 --fps 15 --duration 60` against it and asserts fairness ratio ≤ 1.3, memory growth < 20 %, p99 queue wait reported; then repeats with `SCHEDULER_STRATEGY=fifo` and asserts that queue wait grows (documents why FIFO is not the default) | python script, psutil |

Coverage target: `publicsafe/core` ≥ 90 % lines; overall ≥ 75 %. Tests must not require the internet except `make test-model` (weights download) and `eval fetch`.

---

## M. Implementation phases

Work one milestone at a time. Each milestone ends with: tests listed run and their real output pasted in the report; `README.md`/`.env.example`/`docs/` updated; a short note of decisions taken. Do not start the next milestone until the "proceed when" condition holds and the owner has seen the report.

### M0 — Scaffold, tooling, HTTPS (no features)

- **Scope:** repository layout from E.1; `pyproject.toml` (ruff, black, mypy config), pinned `requirements.txt`; `frontend/` via `npm create vite@latest` (react-ts); `Makefile` targets `setup, certs, dev, run, test, test-browser, test-load, test-model, eval, lint, format`; `.env.example` with every variable in `config.py` and a comment each; `.gitignore` (`.env, data/, certs/, node_modules/, .venv/, *.pt`); `publicsafe certs` wrapper around `mkcert` that creates `certs/dev.pem`/`dev-key.pem` for `localhost`, `127.0.0.1`, the Mac's current LAN IPs and `$(hostname).local`, and prints the iOS/Android root-CA installation steps (also written to `docs/PHONE_SETUP.md`); FastAPI app with `/api/health`; static serving of `frontend/dist`; uvicorn launched with `--ssl-keyfile/--ssl-certfile`; Vite dev server configured with the same certificate and a proxy for `/api` and `/ws`.
- **Files:** everything under `backend/publicsafe/{config,main}.py`, `Makefile`, `docs/SETUP_MACOS.md`, `docs/PHONE_SETUP.md`, `README.md` skeleton.
- **Dependencies:** none.
- **Acceptance:** `make setup && make certs && make dev` serves `https://localhost:8443/api/health` and the Vite page; opening `https://<lan-ip>:5173/` (dev) or `https://<lan-ip>:8443/` (`make run`) on a phone with the root CA installed shows no certificate warning and `window.isSecureContext === true` (documented manual check); `make lint` clean; `make test` runs an empty suite successfully.
- **Tests:** `tests/unit/test_config.py` (env parsing, missing `ADMIN_PASSWORD_HASH` → clear error).
- **Proceed when:** owner confirms HTTPS works on at least one phone, or accepts the documented procedure.

### M1 — Database, admin auth, sessions, cameras, zones (CRUD)

- **Scope:** ORM models and first Alembic migration (all tables in K, even those filled later); SQLite WAL pragmas; Postgres via `DATABASE_URL` smoke-tested with `docker compose up db`; `publicsafe admin set-password`; login/logout/me; sessions, cameras (manual create for tests only, flagged `TEST_MODE`), zones with validation; CSRF header check; security log of logins; rate limit on login.
- **Files:** `db/*`, `migrations/*`, `api/{auth,sessions,cameras,zones}.py`, `core/geometry.py` (polygon validation + normalisation only); frontend `lib/api.ts`, `pages/{Login,Sessions}.tsx`, `pages/SessionDetail.tsx` (camera list without live frames), React Query setup, routing, theme.
- **Dependencies:** M0.
- **Acceptance:** all CRUD endpoints behave per F; unauthenticated → 401; invalid polygon → 400 with reason; Alembic upgrade/downgrade round-trip works on SQLite and Postgres.
- **Tests:** unit geometry validation; integration API tests; security tests for login rate limit and timing; migration test.
- **Proceed when:** `make test` green; schema reviewed by owner (one message).

### M2 — PIN pairing and device credentials (no video yet)

- **Scope:** Section G end-to-end: mint, list, cancel, redeem (atomic), lock, replace-phone; device credentials; `/ws/camera` with `auth`/`hello`/`ping`/`pong`/`pause`/`resume`, status transitions, supersede, revoke; `/ws/dashboard` with `camera_status` and `metrics` (skeleton); security log entries; clock-offset estimator.
- **Files:** `api/pairing.py`, `api/test.py` (TEST_MODE endpoints), `ws/{camera,dashboard,hub}.py`, `core/timing.py`, the 2 s offline sweeper; frontend `lib/ws.ts`, `components/PinModal.tsx` (with QR), `components/CameraTile.tsx` (status only), `pages/Security.tsx`.
- **Dependencies:** M1.
- **Acceptance:** scripted phone client (`tests/integration/fake_phone.py`) pairs and shows `online` on the dashboard socket; all close codes exercised; two concurrent redeems → one 200, one 409; 5th wrong attempt with `code_id` (attempts reaching `max_attempts`=5) → 423; token absent from logs.
- **Tests:** unit pairing; integration WS; security tests for rate limits, PIN/token leakage.
- **Proceed when:** green, and owner approves the PIN format (letters vs digits).

### M3 — Phone capture page and frame ingest with per-camera mailbox

- **Scope:** `/pair` and `/camera` pages (J); capture loop (F-PHONE-*); `frame_meta` + binary ingest, size and rate limits, `LatestFrameSlot`, `Scheduler`, a **pass-through worker** that only decodes and republishes the JPEG to dashboards (no detection yet); `frame_preview` fan-out with throttling and privacy mode; `CameraTile` and `LiveView` showing raw frames; metrics for fps in/processed/drops; `MetricSample` persistence; `loadtest` with `FakeDetector(sleep)` and both strategies.
- **Files:** `frontend/src/pages/{Pair,Camera}.tsx`, `lib/capture.ts`, `components/{LiveView,MetricsPanel}.tsx` (raw frames, metrics), `core/mailbox.py`, `worker/{inference,pipeline}.py` (pipeline without detection), `eval/cli.py` (loadtest only), `pages/Health.tsx`.
- **Dependencies:** M2.
- **Acceptance:** real phone streams to the dashboard at the target fps; pulling the phone's Wi-Fi for 20 s → status `offline` then automatic reconnect → `online`; 3 simulated phones at 30 fps → each ≥ fair share, drops counted, p99 queue wait reported; FIFO mode shows growth in the load test output.
- **Tests:** mailbox/scheduler unit; WS integration for frame ordering and 1009; Playwright with fake camera reaching "Streaming"; load test.
- **Proceed when:** green and the owner has seen a live phone frame on the dashboard.

### M4 — Detection, rules, events, overlays, zone editor

- **Scope:** `Detector` protocol, `UltralyticsDetector`, `FakeDetector`; device auto-select and warm-up; `rules.py` with all rules and invariants; `ZoneStateMachine`; event persistence and fan-out; `frame_result` messages; dashboard overlays; `ZoneEditor`; events page with filters and CSV export; camera settings pushed live; `needs_review` logic; `EVAL_RECORD`.
- **Files:** `core/{detector,rules,state_machine,models}.py`, `worker/pipeline.py` (full), `api/events.py`, `frontend/src/components/{ZoneEditor,LiveView,EventTable}.tsx`, `pages/{CameraDetail,Events}.tsx`.
- **Dependencies:** M3.
- **Acceptance:** walking into a drawn zone on a live phone produces exactly one `zone_entered` and, after leaving, one `zone_cleared`; overlays align with the frame at different window sizes; all rule counts visible per event; privacy mode hides frames but events continue; `make test-model` passes on the golden image.
- **Tests:** geometry + invariants (hypothesis), state machine, CV pipeline with `FakeDetector`, API events/export, Playwright zone drawing and event appearance via `TEST_MODE` scripted detector.
- **Proceed when:** green; owner confirms the demo flow (Section N items 1–4) on real hardware.

### M5 — Evaluation harness and reports

- **Scope:** `eval fetch` (MOT17 with checksum verification recorded after first download), MOT17 and image-sequence/video readers (with the gt column/class confirmation step from I.1), zone and GT loaders, `annotate`, `agreement`, `run` with sweeps and `assert_invariants`, metrics (I.4), bootstrap, McNemar, boundary analysis, `report.md` and figures; `docs/EVALUATION.md` with the experiment table and the success criteria from I.5 copied verbatim; placeholder zone files with `TODO` for the owner.
- **Files:** `eval/{datasets,metrics,report,cli}.py`, `configs/e1_mot17.yaml`, `docs/EVALUATION.md`.
- **Dependencies:** M4.
- **Acceptance:** `eval run` on the 100-frame synthetic fixture reproduces hand-computed numbers; `eval run` on MOT17-04 with placeholder zones completes end-to-end and writes all outputs (numbers are not judged); invariants assertion catches a corrupted result.
- **Tests:** CV eval tests; CLI smoke tests.
- **Proceed when:** green; owner has replaced placeholder zones (or explicitly deferred).

### M6 — Hardening, observability, documentation, Definition of Done

- **Scope:** structured logging with request/camera ids; `/api/metrics` complete; health page; security page; CSP and cookie flags verified; 8-hour soak via load test (memory bound); error states in every view; `docs/API.md` generated from OpenAPI plus the WS message tables; `README.md` with the complete macOS launch procedure, phone setup, troubleshooting (certificate not trusted, camera denied, MPS unavailable), and how to run each experiment; ADRs for decisions in O.4; optional P1 items only if everything above is done and the owner asks (QR already in M2 PinModal; tracking; snapshots; webhooks).
- **Dependencies:** M5.
- **Acceptance:** Definition of Done (N) demonstrated and recorded in `docs/DEMO_CHECKLIST.md` with actual outputs.
- **Tests:** full suite plus soak.

---

## N. Definition of Done

The project is complete when all of the following are demonstrated on one macOS laptop and at least two phones (one iOS Safari, one Android Chrome if available), with outputs recorded in `docs/DEMO_CHECKLIST.md`:

1. `make setup && make certs && make run` starts the system over HTTPS; the README alone is sufficient for a new user to reach this point without GitHub.
2. Admin logs in; creates a session; mints a PIN; phone 1 pairs via `/pair` (QR or typed), reaches "Streaming", appears `online`; a second PIN pairs phone 2; both tiles show independent live frames.
3. A polygon drawn on phone 1's camera is saved in normalised coordinates and rendered identically on the tile, the camera page and after a page reload.
4. A person walking into the zone produces one `zone_entered`, leaving produces one `zone_cleared`; events show camera id, zone id, timestamps and per-rule counts; CSV export opens in a spreadsheet with the documented columns.
5. Pulling phone 1 off Wi-Fi marks it `offline` within 12 s and it reconnects automatically without re-pairing; revoking phone 2 closes its stream with a clear message on the phone.
6. With three streams (two phones + `loadtest --phones 1`), the metrics panel shows per-camera fps, drop ratio and p50/p99 spans, and no camera is starved (fairness ratio ≤ 1.3).
7. Privacy mode on a camera stops binary previews while events continue; no files appear under `data/recordings` unless `EVAL_RECORD=1`.
8. `publicsafe eval run --config configs/e1_mot17.yaml` completes on MOT17-04 and produces `report.md` with per-rule tables, CIs and McNemar results; invariant assertions pass.
9. `make test` passes with no network and no GPU; `make test-browser` passes in Chromium; `make test-model` passes with the real model; `make test-load` passes.
10. No secret, token, PIN or dataset is committed; `.env.example` documents every variable; `docs/API.md`, `docs/EVALUATION.md`, `docs/PHONE_SETUP.md`, `docs/SETUP_MACOS.md` and ADRs exist and match the code.

---

## O. CLI agent operating instructions

### O.1 Before touching code

1. Read this specification completely. Then perform Section B and report.
2. Present a concise implementation plan: the milestone list with any deviations you propose, the exact stack versions you will pin (verify them against PyPI/npm at that moment; do not trust versions in this document blindly), and the questions you need answered. Wait for approval.

### O.2 While working

- Implement one milestone at a time, in order. Within a milestone, commit (if git is initialised) in small steps with descriptive messages; never commit `.env`, certificates, weights, datasets or recordings.
- Preserve any user modifications in files you did not create in the current milestone; if you must change them, explain why.
- Avoid rewrites: extend existing modules; keep public interfaces in `core` stable once M4 is accepted.
- Run the relevant tests after every milestone and paste their actual output (command, summary line, failures). Never report a test as passing that you did not run. If a test cannot run (missing GPU, weights, phone), say so explicitly and mark it "not run".
- Never invent: secrets, credentials, certificate contents, dataset checksums, benchmark numbers, experiment results, or production configuration. Where a value must come from the environment or the owner, leave a clearly named placeholder and list it in the milestone report.
- Keep `docs/` and `.env.example` in sync with the code in the same milestone.
- Record each significant architectural decision as an ADR (`docs/adr/NNNN-title.md`: context, decision, alternatives, consequences). Required ADRs: SQLite default with Postgres option; per-camera mailbox and round-robin scheduler; first-message WebSocket auth; opaque device tokens instead of JWT; client-side overlay rendering; rules computed for all variants every frame; AGPL implications of ultralytics and the `Detector` seam.
- Logging: never log PINs, tokens, cookies, passwords or frame contents.

### O.3 Stop and ask when

- A decision affects **security** (anything in G, cookie/CSP settings, rate limits), **data integrity** (schema changes after M1, event semantics in H.4), or **scientific validity** (membership rule definitions, matching thresholds, metric definitions, success criteria, dataset choice or ground-truth derivation) and this document does not settle it.
- A dependency cannot be installed or a verified version conflicts with another (report the exact error).
- A required external resource is unavailable (dataset mirror, model weights, mkcert) — do not substitute silently.
- The host lacks MPS/CUDA and the CPU path is too slow for the demo targets — report measured numbers and ask whether to lower fps/resolution.
- Anything would require storing images of people beyond what F-PRIV-* allows.

Otherwise proceed autonomously on reversible, well-specified work.

### O.4 Reporting format per milestone

```
## Milestone Mx report
Scope done: …
Files added/changed: …
Decisions (ADRs): …
Tests run: <command> → <summary line>; failures: …
Not run / blocked: …
Placeholders needing owner input: …
Next: Mx+1 after your go-ahead
```

### O.5 Reference values (read from PyPI, npm and vendor pages on 2026-10-10 by the author of this spec; the agent MUST re-verify each before pinning and record the versions it actually installs)

Python 3.12.x (choose the latest 3.12 patch; 3.14 wheels for torch/ultralytics unverified), FastAPI 0.143.0, uvicorn 0.54.0, SQLAlchemy 2.1.4, Alembic 1.20.0, Pydantic 2.14.0, pydantic-settings latest 2.x, ultralytics 8.4.174, torch 2.14.1 (current MPS builds report a macOS 14+ requirement in the PyTorch MPS notes; verify on the target Mac), opencv-python-headless 4.x (pin the latest 4.x; 5.0 exists but is unverified with ultralytics), Shapely 2.2.0, argon2-cffi 25.1.0, slowapi 0.1.10, pytest 9.1.1, httpx 0.28.1, hypothesis latest, Playwright 1.64.0; Node 24 LTS, React 19.3, Vite 8.3, TypeScript 7.0; PostgreSQL 18 (optional); mkcert latest. Browser minimums to document: iOS Safari 16.4 for Screen Wake Lock (caniuse lists partial support from 16.4 and full from 18.4), Android Chrome current.

Facts in this document marked [Verified] were checked against: MDN (getUserMedia secure context, WebSocket constructor), caniuse (Wake Lock), Ultralytics docs (YOLOv8n mAP 37.3, AGPL-3.0, predict/track APIs), Starlette/FastAPI docs (WebSocket API), SQLite docs (WAL), Docker docs and blog (no Metal passthrough in containers), RFC 8628, motchallenge.net (MOT17 contents and CC BY-NC-SA 3.0), EPFL CVLab (WILDTRACK contents; licence not stated). Dataset availability statements about Oxford Town Centre and PETS2009 reflect that their official pages were unreachable on that date and should be rechecked.

---

*End of specification.*
