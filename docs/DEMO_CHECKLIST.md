# Demo checklist (Definition of Done, spec N)

Each item of the Definition of Done with what was actually run and its real
output. Items that need a phone or a person walking in front of a camera are
marked **not run** and are for the owner to do and tick; their automated
stand-ins are listed so it is clear what is and is not covered.

Machine for the runs below: MacBook Air (Apple M1), macOS 26.2, Python 3.12.13,
Node 24.21, Chromium from Playwright. Date: 2026-11-05. Branch
`m5-m6-evaluation-hardening`.

| # | Item | Status |
|---|---|---|
| 1 | `make setup && make certs && make run` over HTTPS; README is enough | Done on the Mac; phone access **not run** |
| 2 | Pair two phones by PIN / QR, both stream | **Not run** (needs phones); automated stand-in passes |
| 3 | Polygon saved normalised, drawn the same on tile, camera page and after reload | Automated stand-in passes; visual check **not run** |
| 4 | Walking into the zone gives one `zone_entered`, leaving one `zone_cleared`; CSV opens | Automated stand-in passes; real person **not run** |
| 5 | Wi-Fi off -> offline within 12 s and reconnect; revoke closes the stream | Revoke automated; Wi-Fi test **not run** |
| 6 | Three streams, per-camera metrics, fairness ≤ 1.3 | Done with 3 simulated phones; 2 phones + 1 simulated **not run** |
| 7 | Privacy mode stops previews; no files in `data/recordings` without `EVAL_RECORD=1` | Done (test + check) |
| 8 | `eval run` on MOT17-04 writes `report.md` with per-rule tables, CIs, McNemar; invariants pass | Done (placeholder zones, derived GT) |
| 9 | `make test`, `make test-browser`, `make test-model`, `make test-load` pass | Done |
| 10 | No secret / token / PIN / dataset committed; `.env.example` complete; docs match the code | Done |

## 1. Setup and HTTPS start

```text
$ make setup            # uv venv (Python 3.12.13), pinned deps, npm ci, .env
Setup complete.
$ make certs
Created a new certificate valid for the following names ... (localhost, 127.0.0.1, LAN IPs, <host>.local)
$ echo "<generated>" | .venv/bin/publicsafe admin set-password --password-stdin
Restart the server for the new password to take effect.
$ .venv/bin/publicsafe run --host 127.0.0.1 --port 8460      # what `make run` starts, after `make build`
public.safe 0.1.0 listening on https://127.0.0.1:8460
{"t": "...", "level": "info", "logger": "publicsafe", "msg": "public.safe 0.1.0 started",
 "detector": "yolo", "device": "mps", "model_sha256": "f59b3d83...", "scheduler": "latest", "test_mode": false}
$ curl --cacert "$(mkcert -CAROOT)/rootCA.pem" -D - https://localhost:8460/api/health
HTTP/1.1 200 OK
content-security-policy: default-src 'self'; img-src 'self' blob: data:; connect-src 'self' wss:
x-content-type-options: nosniff
x-frame-options: DENY
x-request-id: 29851d4a16c587bb
{"status":"ok","version":"0.1.0","uptime_s":9.6,"device":"mps","model_sha256":"f59b3d83...","db":"ok"}
$ curl ... -d '{"username":"admin","password":"..."}' https://localhost:8460/api/auth/login
HTTP/1.1 204 No Content
set-cookie: ps_admin=<redacted>; HttpOnly; Max-Age=43200; Path=/; SameSite=strict; Secure
```

The matching access log line:
`{"logger": "publicsafe.access", "msg": "POST /api/auth/login 204", "request_id": "2271e61c878e58a5", "status": 204, "duration_ms": 63.56}`.

Owner: open `https://<lan-ip>:8443/` on a phone with the root CA installed and
confirm no certificate warning ([PHONE_SETUP.md](PHONE_SETUP.md) section 4).

## 2-5. Phones, zones, events, reconnect (owner, real hardware)

Automated stand-ins that passed on 2026-11-05 (`make test-browser`, Chromium
with a fake camera, TEST_MODE backend on :8445):

```text
✓ e2e/pairing.spec.ts › phone pairs with a PIN, streams with a fake camera, dashboard tile goes online, revoke stops it (3.6s)
✓ e2e/pairing.spec.ts › pair page rejects a wrong code and counts attempts (2.7s)
✓ e2e/zones.spec.ts › draw a zone on the live frame, scripted detections produce an event (4.5s)
3 passed (16.6s)
```

and in `make test`: `tests/cv/test_pipeline_events.py` (scripted 60-frame
sequence -> exactly the expected `zone_entered` / `occupancy_changed` /
`zone_cleared`), `tests/integration/test_api_events.py` (CSV columns),
`tests/integration/test_ws_camera.py` (close codes, revoke 4003, supersede
4004).

To do on real hardware, filling in the table:

| Check | iPhone (Safari) | Android (Chrome) |
|---|---|---|
| 2. Phone 1 pairs by PIN or QR, reaches "Streaming", tile online; phone 2 pairs too; both tiles live | | |
| 3. Draw a polygon on phone 1's camera; same shape on tile, camera page and after reload | | |
| 4. Walk in: one `zone_entered`; walk out: one `zone_cleared`; event shows camera, zone, times, per-rule counts | | |
| 4. Events page -> CSV export opens in a spreadsheet with the documented columns | | |
| 5. Wi-Fi off on phone 1: `offline` within 12 s; Wi-Fi on: reconnects without re-pairing | | |
| 5. Revoke phone 2: stream stops, the phone shows the revoked message | | |

Also the manual list in [PHONE_SETUP.md](PHONE_SETUP.md) section 5.

## 6. Three streams and fairness

`make test-load` (3 simulated phones at 15 fps through the real pairing and
WebSocket path, `FakeDetector` 20 ms):

```text
== 3 phone(s) @ 15.0 fps for 60.0 s, strategy=latest ==
camera         sent     in   proc  fps_in  fps_pr   drop   tot50   tot99    qw50    qw99
loadtest-1      900    900    783    15.0    13.1  0.130    59.6    93.2    34.7    66.9
loadtest-2      900    900    782    15.0    13.0  0.131    59.5    92.2    33.7    66.7
loadtest-3      900    900    782    15.0    13.0  0.131    59.4    91.7    33.9    65.7
fairness ratio (max/min processed fps): 1.00   server mem 152.7 -> 132.7 MB   backlog at end: 0
...
latest: fairness 1.0015349194167307, mem 152.7 -> 132.7 MB, queue_wait p99 max 66.864990234375 ms
fifo:   queue_wait p99 max 7632.014892578125 ms, backlog 299
PASS
```

With the real YOLOv8n and 1280×720 frames (3 × 10 fps): every camera at
10.0 processed fps, total p99 ≤ 69 ms, fairness 1.00 (details in
[EVALUATION.md](EVALUATION.md)). Owner: repeat with two real phones plus
`publicsafe loadtest --phones 1` and look at the metrics panel.

## 7. Privacy and recordings

`tests/integration/test_frames.py::test_frames_reach_dashboard_with_preview_and_privacy_mode`
checks that privacy mode keeps sending `frame_result` JSON but no binary
previews, and that `last-frame.jpg` answers 404 `privacy_mode`. After the HTTPS run in item 1
(default `EVAL_RECORD=false`):

```text
$ ls data/recordings
ls: data/recordings: No such file or directory
```

## 8. Evaluation on MOT17

```text
$ publicsafe eval fetch mot17          # second run: files present, only the directory is read (9 s)
MOT17-02: 602 files, 106.6 MB, manifest 12c04bd5... (verified)
MOT17-04: 1052 files, 248.1 MB, manifest ca02ae40... (verified)
MOT17-09: 527 files, 110.8 MB, manifest d171da61... (verified)
$ make eval CONFIG=configs/e1_mot17_derived.yaml SWEEP="enter_frames=1,3,5 exit_frames=1,5,10"
rule                   precision  recall      f1  event_f1
bbox_overlap               0.454   0.635   0.530     0.378
bottom_center              0.920   0.616   0.738     0.680
three_point_any            0.804   0.620   0.700     0.571
three_point_majority       0.921   0.615   0.737     0.596
three_point_all            0.934   0.563   0.703     0.604
bottom_strip               0.879   0.613   0.723     0.571
wrote data/eval/e1-mot17-derived-2026-11-05
$ publicsafe eval check data/eval/e1-mot17-derived-2026-11-05
invariants hold on every frame
```

The report (MOT17-02, -04 and -09) is committed at
[results/e1-mot17-derived-2026-11-05/report.md](results/e1-mot17-derived-2026-11-05/report.md).
It uses the placeholder zones and **derived** GT, so it is a sanity check;
`configs/e1_mot17.yaml` with the owner's manual GT is the headline run.

## 9. Test suites

```text
$ make test
253 passed, 1 skipped, 2 deselected        # skipped: PostgreSQL round-trip (needs docker compose up -d db)
$ make test-model
device=mps warmup=4781 ms detections=3 in_zone=3 sha256=f59b3d833e2f
fp32 27.9 ms/frame conf=[0.83, 0.85, 0.87]
fp16 29.6 ms/frame conf=[0.82, 0.85, 0.87]
2 passed
$ make test-browser
3 passed (16.6s)
$ make test-load
PASS
$ make lint
ruff, black, mypy (strict core), eslint, prettier, tsc: clean
```

Soak (`make test-soak`, Section D reliability): see the table below. The
required run is 8 hours; the run done here is shorter and says so.

| Run | Duration | Phones / fps | RSS after warm-up -> end | Growth | Result |
|---|---|---|---|---|---|
| 2026-11-05 | 1 h | 3 / 10 (FakeDetector 20 ms) | SOAK_PENDING | | |
| 8-hour run | 8 h | 3 / 10 | **not run** (owner: `make test-soak`, leave the Mac on) | | |

## 10. Secrets, datasets, docs

- `git ls-files` contains no `.pt`, `.zip`, `.db`, certificate, dataset or
  eval output files; `data/` is ignored except `data/zones/`.
- `detect-secrets scan` on all tracked files: only the expected hits (SHA-256
  hashes of the model, datasets and git commits; test passwords in tests; the
  local PostgreSQL password in `docker-compose.yml` and `.env.example`).
- Every field of `Settings` has a line in `.env.example` (checked with a
  script against `Settings.model_fields`).
- `docs/API.md` is generated from the code and `tests/unit/test_apidoc.py`
  fails if it is stale; `docs/EVALUATION.md`, `docs/PHONE_SETUP.md`,
  `docs/SETUP_MACOS.md` and ADRs 0001-0011 are up to date.
