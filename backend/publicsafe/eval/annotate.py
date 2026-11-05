"""``publicsafe eval annotate``: a local page for labelling ``in_zone`` per GT person (I.3).

Served on ``http://127.0.0.1:<port>`` (localhost is a secure context, so no
certificate and no login are needed). Each k-th frame is shown with its MOT GT
boxes and the polygon; the annotator clicks a box (or uses Tab / Space) to
toggle ``in_zone`` and presses Enter to save and move on. Rows are appended to
``data/gt/<sequence>.<zone>.csv`` with ``source=manual``; re-labelling a frame
appends again and the last row wins when the file is read.

The page is a small standalone copy of the dashboard's canvas overlay
(``frontend/src/components/LiveView.tsx``): the React bundle needs the admin
session, this tool must work without one.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from publicsafe.eval.datasets import (
    NO_PERSON,
    DatasetError,
    GtBox,
    ZoneSpec,
    append_gt_rows,
    read_gt_csv,
    read_mot_gt,
    read_seqinfo,
    split_gt,
)


@dataclass
class AnnotateSession:
    seq_dir: Path
    sequence: str
    zone: ZoneSpec
    out: Path
    annotator: str
    every: int = 5
    gt_classes: tuple[int, ...] = (1,)
    min_visibility: float = 0.25

    def __post_init__(self) -> None:
        info = read_seqinfo(self.seq_dir)
        self.w, self.h = info.width, info.height
        self.frames = list(range(1, info.length + 1, self.every))
        self._boxes = read_mot_gt(self.seq_dir / "gt" / "gt.txt")
        self._lock = threading.Lock()

    def persons(self, frame: int) -> list[GtBox]:
        counted, _ = split_gt(self._boxes.get(frame, []), self.gt_classes, self.min_visibility)
        return sorted(counted, key=lambda b: b.person_id)

    def existing(self) -> dict[tuple[int, int], int]:
        if not self.out.exists():
            return {}
        return read_gt_csv(self.out).labels

    def done(self) -> list[int]:
        if not self.out.exists():
            return []
        return read_gt_csv(self.out).frames

    def image_path(self, frame: int) -> Path:
        return self.seq_dir / "img1" / f"{frame:06d}.jpg"

    def state(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "zone": self.zone.name,
            "points": self.zone.points,
            "w": self.w,
            "h": self.h,
            "annotator": self.annotator,
            "frames": self.frames,
            "done": self.done(),
        }

    def frame(self, frame: int) -> dict[str, Any]:
        if frame not in self.frames:
            raise DatasetError(f"frame {frame} is not one of the frames to annotate")
        labels = self.existing()
        return {
            "frame": frame,
            "persons": [
                {
                    "person_id": b.person_id,
                    "bbox": [round(v, 1) for v in b.bbox],
                    "in_zone": labels.get((frame, b.person_id)),
                }
                for b in self.persons(frame)
            ],
        }

    def save(self, frame: int, labels: list[dict[str, Any]]) -> int:
        """Append one frame's labels; every GT person on the frame must be labelled."""
        if frame not in self.frames:
            raise DatasetError(f"frame {frame} is not one of the frames to annotate")
        ids = {b.person_id for b in self.persons(frame)}
        given = {int(item["person_id"]): int(item["in_zone"]) for item in labels}
        if set(given) != ids:
            raise DatasetError(f"frame {frame}: labels must cover exactly persons {sorted(ids)}")
        if any(v not in (0, 1) for v in given.values()):
            raise DatasetError("in_zone must be 0 or 1")
        rows = [(frame, pid, given[pid], "manual", self.annotator) for pid in sorted(given)]
        if not rows:
            rows = [(frame, NO_PERSON, 0, "manual", self.annotator)]
        with self._lock:
            append_gt_rows(self.out, rows)
        return len(rows)


def make_handler(session: AnnotateSession) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "publicsafe-annotate"

        def log_message(self, format: str, *args: Any) -> None:
            return  # quiet; the CLI prints its own progress

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            try:
                if self.path == "/":
                    self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path == "/api/state":
                    self._json(HTTPStatus.OK, session.state())
                elif match := re.fullmatch(r"/api/frame/(\d+)", self.path):
                    self._json(HTTPStatus.OK, session.frame(int(match.group(1))))
                elif match := re.fullmatch(r"/img/(\d+)\.jpg", self.path):
                    frame = int(match.group(1))
                    if frame not in session.frames:
                        raise DatasetError("unknown frame")
                    self._send(HTTPStatus.OK, session.image_path(frame).read_bytes(), "image/jpeg")
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except (DatasetError, OSError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:
            if self.path != "/api/label":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(min(length, 1_000_000)) or b"{}")
                written = session.save(int(body["frame"]), list(body.get("labels", [])))
                self._json(HTTPStatus.OK, {"ok": True, "rows": written})
            except (DatasetError, KeyError, TypeError, ValueError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def serve(session: AnnotateSession, port: int = 8765) -> ThreadingHTTPServer:
    """Bind to 127.0.0.1 only; the caller runs ``serve_forever()``."""
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(session))


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>public.safe - annotate in_zone</title>
<style>
 body{font:14px/1.4 -apple-system,system-ui,sans-serif;margin:0;background:#111;color:#eee}
 header{display:flex;gap:1rem;align-items:center;padding:.5rem 1rem;background:#1c1c1c}
 header b{font-weight:600} main{padding:.5rem 1rem}
 canvas{max-width:100%;height:auto;display:block;cursor:pointer;outline:none}
 canvas:focus{box-shadow:0 0 0 2px #4c8bf5}
 .keys{color:#aaa} .saved{color:#7bd389} .err{color:#ff7a7a}
 button{font:inherit;padding:.2rem .7rem}
</style></head><body>
<header><b id="title">loading…</b><span id="pos"></span><span id="status"></span>
<button id="prev">← Prev</button><button id="save">Save &amp; next ↵</button>
<button id="next">Skip →</button></header>
<main>
<p class="keys">Click a box (or Tab / Shift+Tab to select, Space to toggle) to mark the person
<b>inside the zone</b>. Unmarked persons are saved as outside. Enter saves and opens the next
frame; ← / → move without saving. Green = in zone, grey = outside, outline = selected.</p>
<canvas id="c" tabindex="0" aria-label="frame with ground-truth boxes"></canvas>
</main>
<script>
"use strict";
const c = document.getElementById("c"), ctx = c.getContext("2d");
const statusEl = document.getElementById("status");
let state = null, idx = 0, persons = [], sel = 0, img = new Image();
async function api(path, opts) {
  const r = await fetch(path, opts); const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status); return j;
}
function draw() {
  if (!state) return;
  c.width = state.w; c.height = state.h;
  ctx.drawImage(img, 0, 0, state.w, state.h);
  ctx.lineWidth = 4; ctx.strokeStyle = "#ffcc00"; ctx.fillStyle = "rgba(255,204,0,0.12)";
  ctx.beginPath();
  state.points.forEach(([x, y], i) => i ? ctx.lineTo(x * state.w, y * state.h) : ctx.moveTo(x * state.w, y * state.h));
  ctx.closePath(); ctx.fill(); ctx.stroke();
  ctx.font = "bold 22px system-ui";
  persons.forEach((p, i) => {
    const [x1, y1, x2, y2] = p.bbox, inz = p.in_zone === 1;
    ctx.lineWidth = i === sel ? 6 : 3;
    ctx.strokeStyle = inz ? "#2ecc71" : "#9aa0a6";
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    ctx.fillStyle = inz ? "#2ecc71" : "#9aa0a6";
    ctx.beginPath(); ctx.arc((x1 + x2) / 2, y2, 6, 0, 7); ctx.fill();
    ctx.fillText((inz ? "IN " : "") + p.person_id, x1 + 2, y1 - 6);
  });
}
async function load(i) {
  idx = Math.max(0, Math.min(state.frames.length - 1, i));
  const f = state.frames[idx];
  const data = await api("/api/frame/" + f);
  persons = data.persons.map(p => ({...p, in_zone: p.in_zone === 1 ? 1 : 0}));
  sel = 0;
  document.getElementById("pos").textContent =
    `frame ${f} (${idx + 1}/${state.frames.length}), ${state.done.length} saved`;
  img = new Image(); img.onload = draw; img.src = "/img/" + f + ".jpg";
  statusEl.textContent = state.done.includes(f) ? "already saved" : ""; statusEl.className = "saved";
  c.focus();
}
async function save() {
  const f = state.frames[idx];
  try {
    await api("/api/label", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({frame: f, labels: persons.map(p => ({person_id: p.person_id, in_zone: p.in_zone}))})});
    if (!state.done.includes(f)) state.done.push(f);
    await load(idx + 1); statusEl.textContent = "saved " + f; statusEl.className = "saved";
  } catch (e) { statusEl.textContent = e.message; statusEl.className = "err"; }
}
c.addEventListener("click", ev => {
  const r = c.getBoundingClientRect(), sx = state.w / r.width, sy = state.h / r.height;
  const x = (ev.clientX - r.left) * sx, y = (ev.clientY - r.top) * sy;
  let best = -1, area = Infinity;
  persons.forEach((p, i) => { const [x1, y1, x2, y2] = p.bbox;
    if (x >= x1 && x <= x2 && y >= y1 && y <= y2 && (x2 - x1) * (y2 - y1) < area) { best = i; area = (x2 - x1) * (y2 - y1); } });
  if (best >= 0) { sel = best; persons[best].in_zone ^= 1; draw(); }
});
document.addEventListener("keydown", ev => {
  if (ev.key === "Enter") { ev.preventDefault(); save(); }
  else if (ev.key === "ArrowRight") load(idx + 1);
  else if (ev.key === "ArrowLeft") load(idx - 1);
  else if (ev.key === "Tab" && persons.length) { ev.preventDefault();
    sel = (sel + (ev.shiftKey ? persons.length - 1 : 1)) % persons.length; draw(); }
  else if (ev.key === " " && persons.length) { ev.preventDefault(); persons[sel].in_zone ^= 1; draw(); }
});
document.getElementById("save").onclick = save;
document.getElementById("next").onclick = () => load(idx + 1);
document.getElementById("prev").onclick = () => load(idx - 1);
(async () => {
  state = await api("/api/state");
  document.getElementById("title").textContent = `${state.sequence} · zone ${state.zone} · ${state.annotator}`;
  const first = state.frames.findIndex(f => !state.done.includes(f));
  await load(first < 0 ? 0 : first);
})();
</script></body></html>
"""
