"""``make test-load``: start a TEST_MODE server with FakeDetector(sleep=20 ms), run the load test.

Asserts (Section L): fairness ratio <= 1.3, memory growth < 20 %, p99 queue wait reported;
then repeats with SCHEDULER_STRATEGY=fifo and asserts that queue wait grows.
Duration per run: LOADTEST_DURATION seconds (default 60).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
PY = Path(sys.executable)
DURATION = float(os.environ.get("LOADTEST_DURATION", "60"))
PHONES = os.environ.get("LOADTEST_PHONES", "3")
FPS = os.environ.get("LOADTEST_FPS", "15")
PASSWORD = "loadtest-password"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def make_images(folder: Path, n: int = 12) -> None:
    rng = np.random.default_rng(1234)
    for i in range(n):
        img = np.full((360, 640, 3), 90, np.uint8)
        cv2.rectangle(img, (40 + i * 20, 120), (140 + i * 20, 340), (30, 200, 60), -1)
        img = np.clip(img.astype(np.int16) + rng.integers(-20, 20, img.shape), 0, 255).astype(
            np.uint8
        )
        cv2.imwrite(str(folder / f"frame_{i:03d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 65])


def start_server(workdir: Path, port: int, strategy: str, hash_: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": hash_,
        "DATABASE_URL": f"sqlite+pysqlite:///{workdir / f'load-{strategy}.db'}",
        "TEST_MODE": "1",
        "DETECTOR": "fake",
        "FAKE_DETECTOR_SLEEP_MS": "20",
        "SCHEDULER_STRATEGY": strategy,
        "LOG_LEVEL": "WARNING",
        "FRONTEND_DIST": str(workdir / "no-dist"),
    }
    proc = subprocess.Popen(
        [
            str(PY),
            "-m",
            "publicsafe.cli",
            "run",
            "--no-tls",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT / "backend",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200:
                    return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    out = proc.stdout.read().decode() if proc.stdout else ""
    raise SystemExit(f"server did not start:\n{out}")


def run_loadtest(port: int, strategy: str, images: Path, out: Path) -> list[dict]:
    cmd = [
        str(PY),
        "-m",
        "publicsafe.cli",
        "loadtest",
        "--phones",
        PHONES,
        "--fps",
        FPS,
        "--source",
        str(images),
        "--duration",
        str(DURATION),
        "--server",
        f"http://127.0.0.1:{port}",
        "--admin-user",
        "admin",
        "--admin-password",
        PASSWORD,
        "--queue",
        strategy,
        "--out",
        str(out),
    ]
    subprocess.run(cmd, cwd=ROOT / "backend", check=True)
    return json.loads(out.read_text())


def main() -> int:
    from argon2 import PasswordHasher

    hash_ = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        images = work / "frames"
        images.mkdir()
        make_images(images)
        results: dict[str, dict] = {}
        for strategy in ("latest", "fifo"):
            port = free_port()
            proc = start_server(work, port, strategy, hash_)
            try:
                results[strategy] = run_loadtest(port, strategy, images, work / f"{strategy}.json")[
                    0
                ]
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    latest, fifo = results["latest"], results["fifo"]
    fr = latest["fairness_ratio"]
    if fr is None or fr > 1.3:
        failures.append(f"fairness ratio {fr} > 1.3 under latest")
    growth = (latest["server_mem_mb_end"] - latest["server_mem_mb_start"]) / max(
        latest["server_mem_mb_start"], 1
    )
    if growth >= 0.20:
        failures.append(f"memory grew {growth:.0%} during the latest run")
    qw_latest = [c["queue_wait_p99"] for c in latest["cameras"] if c["queue_wait_p99"] is not None]
    qw_fifo = [c["queue_wait_p99"] for c in fifo["cameras"] if c["queue_wait_p99"] is not None]
    if not qw_latest:
        failures.append("p99 queue wait not reported under latest")
    if qw_latest and qw_fifo and max(qw_fifo) <= max(qw_latest):
        failures.append(
            f"fifo p99 queue wait {max(qw_fifo)} did not exceed latest {max(qw_latest)}"
        )
    if (
        fifo["scheduler_backlog_end"] is not None
        and fifo["scheduler_backlog_end"] == 0
        and max(qw_fifo or [0]) < 1000
    ):
        print(
            "note: fifo backlog drained by the end (short run); queue-wait comparison still applies"
        )

    print("\n== summary ==")
    mem_start, mem_end = latest["server_mem_mb_start"], latest["server_mem_mb_end"]
    qw_l = max(qw_latest) if qw_latest else None
    qw_f = max(qw_fifo) if qw_fifo else None
    print(f"latest: fairness {fr}, mem {mem_start} -> {mem_end} MB, queue_wait p99 max {qw_l} ms")
    print(f"fifo:   queue_wait p99 max {qw_f} ms, backlog {fifo['scheduler_backlog_end']}")
    if failures:
        print("FAILED:\n  " + "\n  ".join(failures))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
