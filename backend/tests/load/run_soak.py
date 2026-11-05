"""``make test-soak``: the reliability requirement of Section D (8 hours, memory growth <= 20 %).

Starts a TEST_MODE server (FakeDetector sleeping 20 ms, as in ``make test-load``),
streams ``SOAK_PHONES`` fake phones at ``SOAK_FPS`` through the real pairing and
WebSocket path for ``SOAK_DURATION`` seconds (default 8 h) and samples the server
process's RSS every ``SOAK_SAMPLE_S`` seconds.

Growth is measured from the warm-up baseline (median RSS of the samples between
``SOAK_WARMUP_S`` and twice that) to the median of the last five samples, so the
one-off allocations at start (imports, first frames, SQLite cache) do not count.
The samples and a summary are written to ``data/soak/<timestamp>/``.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_loadtest import (
    PASSWORD,
    PY,
    ROOT,
    free_port,
    make_images,
    start_server,
)

DURATION = float(os.environ.get("SOAK_DURATION", str(8 * 3600)))
PHONES = os.environ.get("SOAK_PHONES", "3")
FPS = os.environ.get("SOAK_FPS", "10")
SAMPLE_S = float(os.environ.get("SOAK_SAMPLE_S", "30"))
WARMUP_S = float(os.environ.get("SOAK_WARMUP_S", str(min(600.0, DURATION / 10))))
MAX_GROWTH = 0.20


def sample(
    proc: psutil.Process, stop: threading.Event, rows: list[tuple[float, float, float]]
) -> None:
    start = time.monotonic()
    proc.cpu_percent(None)
    while not stop.is_set():
        try:
            rss = proc.memory_info().rss / 1e6
            cpu = proc.cpu_percent(None)
        except psutil.Error:
            return
        rows.append((round(time.monotonic() - start, 1), round(rss, 1), cpu))
        stop.wait(SAMPLE_S)


def main() -> int:
    from argon2 import PasswordHasher

    hash_ = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    out_dir = ROOT / "data" / "soak" / dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[float, float, float]] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        images = work / "frames"
        images.mkdir()
        make_images(images)
        port = free_port()
        server = start_server(work, port, "latest", hash_)
        log_file = (out_dir / "server.log").open("wb")
        drain = threading.Thread(  # a full pipe would block the server on a long run
            target=lambda: [log_file.write(line) for line in iter(server.stdout.readline, b"")],
            daemon=True,
        )
        drain.start()
        stop = threading.Event()
        sampler = threading.Thread(
            target=sample, args=(psutil.Process(server.pid), stop, rows), daemon=True
        )
        sampler.start()
        try:
            result = subprocess.run(
                [
                    str(PY), "-m", "publicsafe.cli", "loadtest",
                    "--phones", PHONES, "--fps", FPS, "--source", str(images),
                    "--duration", str(DURATION), "--server", f"http://127.0.0.1:{port}",
                    "--admin-user", "admin", "--admin-password", PASSWORD,
                    "--queue", "latest", "--out", str(out_dir / "loadtest.json"),
                ],
                cwd=ROOT / "backend",
            )  # fmt: skip
        finally:
            stop.set()
            sampler.join(5)
            alive = server.poll() is None
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
            drain.join(5)
            log_file.close()

    with (out_dir / "memory.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["t_s", "rss_mb", "cpu_percent"])
        writer.writerows(rows)

    failures: list[str] = []
    if result.returncode != 0:
        failures.append(f"loadtest exited with {result.returncode}")
    if not alive:
        failures.append("server process died during the soak")
    base = [r[1] for r in rows if WARMUP_S <= r[0] < 2 * WARMUP_S] or [r[1] for r in rows[:5]]
    tail = [r[1] for r in rows[-5:]]
    baseline = statistics.median(base) if base else None
    final = statistics.median(tail) if tail else None
    growth = None if not baseline or final is None else final / baseline - 1
    if growth is None:
        failures.append("no memory samples")
    elif growth > MAX_GROWTH:
        failures.append(f"memory grew {growth:.1%} (> {MAX_GROWTH:.0%})")
    fairness = None
    if (out_dir / "loadtest.json").is_file():
        fairness = json.loads((out_dir / "loadtest.json").read_text())[0]["fairness_ratio"]
        if fairness is None or fairness > 1.3:
            failures.append(f"fairness ratio {fairness} > 1.3")
    summary = {
        "duration_s": DURATION,
        "phones": int(PHONES),
        "fps": float(FPS),
        "samples": len(rows),
        "rss_baseline_mb": baseline,
        "rss_final_mb": final,
        "rss_max_mb": max((r[1] for r in rows), default=None),
        "growth": growth,
        "fairness_ratio": fairness,
        "passed": not failures,
        "failures": failures,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("\n== soak summary ==")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out_dir}")
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
