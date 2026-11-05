"""``publicsafe loadtest`` (F-EVAL-3) and the ``publicsafe eval`` sub-commands (F-EVAL-1, I)."""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from pathlib import Path
from typing import Any

import httpx
import typer
import websockets

from publicsafe.core.timing import now_ms

CSRF = {"X-Requested-With": "publicsafe"}
IMAGE_SUFFIXES = (".jpg", ".jpeg")


class LoadTestError(RuntimeError):
    pass


def ws_url_for(server: str) -> str:
    if server.startswith("https://"):
        return "wss://" + server[len("https://") :].rstrip("/") + "/ws/camera"
    if server.startswith("http://"):
        return "ws://" + server[len("http://") :].rstrip("/") + "/ws/camera"
    raise LoadTestError("--server must start with http:// or https://")


def load_images(source: Path) -> list[bytes]:
    files = sorted(p for p in source.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise LoadTestError(f"no .jpg files in {source}")
    return [p.read_bytes() for p in files]


def fairness_ratio(rows: list[dict[str, Any]]) -> float | None:
    rates = [r["fps_processed"] for r in rows if r.get("fps_processed") is not None]
    if not rates or min(rates) <= 0:
        return None
    return max(rates) / min(rates)


async def stream_phone(
    ws_url: str,
    token: str,
    images: list[bytes],
    fps: float,
    duration_s: float,
    ssl_ctx: ssl.SSLContext | None,
    label: str,
) -> dict[str, Any]:
    """One fake phone: auth, then frames at ``fps`` plus a ping every 5 s."""
    sent = acks = 0
    last_ack: dict[str, Any] | None = None
    async with websockets.connect(ws_url, ssl=ssl_ctx, max_size=4 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token, "client": {"ua": label}}))
        hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if hello.get("type") != "hello":
            raise LoadTestError(f"{label}: expected hello, got {hello}")

        async def reader() -> None:
            nonlocal acks, last_ack
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "ack":
                        acks += 1
                        last_ack = msg
            except websockets.ConnectionClosed:
                pass

        reader_task = asyncio.create_task(reader())
        interval = 1.0 / fps
        start = time.monotonic()
        next_frame = start
        next_ping = start
        seq = 0
        last_rtt: float | None = None
        try:
            while time.monotonic() - start < duration_s:
                now = time.monotonic()
                if now >= next_ping:
                    await ws.send(
                        json.dumps(
                            {"type": "ping", "t_phone_ms": now_ms(), "last_rtt_ms": last_rtt}
                        )
                    )
                    next_ping = now + 5.0
                if now >= next_frame:
                    seq += 1
                    data = images[(seq - 1) % len(images)]
                    await ws.send(
                        json.dumps(
                            {
                                "type": "frame_meta",
                                "seq": seq,
                                "t_phone_ms": now_ms(),
                                "w": 0,
                                "h": 0,
                            }
                        )
                    )
                    await ws.send(data)
                    sent += 1
                    next_frame += interval
                    if next_frame < now - interval:  # fell behind: resynchronise
                        next_frame = now
                await asyncio.sleep(max(0.0, min(next_frame, next_ping) - time.monotonic()))
        finally:
            reader_task.cancel()
    return {"label": label, "sent": sent, "acks": acks, "last_ack": last_ack}


async def run_one(
    client: httpx.AsyncClient,
    server: str,
    phones: int,
    fps: float,
    images: list[bytes],
    duration_s: float,
    ssl_ctx: ssl.SSLContext | None,
) -> dict[str, Any]:
    session = (
        await client.post("/api/sessions", json={"name": f"loadtest-{phones}p-{int(time.time())}"})
    ).json()
    cameras: list[tuple[str, str]] = []
    for i in range(phones):
        mint = (
            await client.post(
                f"/api/sessions/{session['id']}/pairing-codes",
                json={"camera_name_hint": f"loadtest-{i + 1}"},
            )
        ).json()
        redeem = await client.post(
            "/api/pair/redeem",
            json={
                "pin": mint["pin"],
                "code_id": mint["code_id"],
                "device_label": f"loadtest-{i + 1}",
            },
        )
        redeem.raise_for_status()
        cameras.append((redeem.json()["camera_id"], redeem.json()["device_token"]))

    before = (await client.get("/api/metrics")).json()["server"]
    ws_url = ws_url_for(server)
    phone_results = await asyncio.gather(
        *(
            stream_phone(ws_url, token, images, fps, duration_s, ssl_ctx, f"loadtest-{i + 1}")
            for i, (_, token) in enumerate(cameras)
        )
    )
    await asyncio.sleep(1.0)  # let the worker drain and the sampler catch up
    metrics = (await client.get("/api/metrics")).json()
    rows = []
    for i, (camera_id, _) in enumerate(cameras):
        cam = metrics["cameras"].get(camera_id, {})
        spans = cam.get("spans") or {}
        rows.append(
            {
                "camera": f"loadtest-{i + 1}",
                "camera_id": camera_id,
                "sent": phone_results[i]["sent"],
                "frames_in": cam.get("frames_in"),
                "processed": cam.get("processed"),
                "fps_in": round((cam.get("frames_in") or 0) / duration_s, 2),
                "fps_processed": round((cam.get("processed") or 0) / duration_s, 2),
                "drop_ratio": cam.get("drop_ratio"),
                "dropped_replaced": cam.get("dropped_replaced"),
                "dropped_ratelimit": cam.get("dropped_ratelimit"),
                "total_p50": (spans.get("total") or {}).get("p50"),
                "total_p99": (spans.get("total") or {}).get("p99"),
                "queue_wait_p50": (spans.get("queue_wait") or {}).get("p50"),
                "queue_wait_p99": (spans.get("queue_wait") or {}).get("p99"),
                "infer_p50": (spans.get("infer") or {}).get("p50"),
            }
        )
    for camera_id, _ in cameras:
        await client.post(f"/api/cameras/{camera_id}/revoke", json={"reason": "loadtest_done"})
    await client.patch(f"/api/sessions/{session['id']}", json={"archived": True})
    return {
        "phones": phones,
        "fps": fps,
        "duration_s": duration_s,
        "strategy": metrics["server"]["scheduler_strategy"],
        "server_mem_mb_start": before["mem_mb"],
        "server_mem_mb_end": metrics["server"]["mem_mb"],
        "server_cpu_percent": metrics["server"]["cpu_percent"],
        "scheduler_backlog_end": metrics["server"].get("scheduler_backlog"),
        "fairness_ratio": fairness_ratio(rows),
        "cameras": rows,
    }


def print_table(result: dict[str, Any]) -> None:
    typer.echo(
        f"\n== {result['phones']} phone(s) @ {result['fps']} fps for {result['duration_s']} s, "
        f"strategy={result['strategy']} =="
    )
    columns = [
        ("camera", 12),
        ("sent", 7),
        ("in", 7),
        ("proc", 7),
        ("fps_in", 8),
        ("fps_pr", 8),
        ("drop", 7),
        ("tot50", 8),
        ("tot99", 8),
        ("qw50", 8),
        ("qw99", 8),
    ]
    typer.echo(
        "".join(f"{name:<{w}}" if i == 0 else f"{name:>{w}}" for i, (name, w) in enumerate(columns))
    )
    for r in result["cameras"]:

        def f(v: Any, nd: int = 1) -> str:
            return "-" if v is None else f"{v:.{nd}f}"

        typer.echo(
            f"{r['camera']:<12}{r['sent']:>7}{r['frames_in'] or 0:>7}{r['processed'] or 0:>7}"
            f"{f(r['fps_in']):>8}{f(r['fps_processed']):>8}{f(r['drop_ratio'], 3):>7}"
            f"{f(r['total_p50']):>8}{f(r['total_p99']):>8}{f(r['queue_wait_p50']):>8}{f(r['queue_wait_p99']):>8}"
        )
    fr = result["fairness_ratio"]
    typer.echo(
        f"fairness ratio (max/min processed fps): {'-' if fr is None else f'{fr:.2f}'}   "
        f"server mem {result['server_mem_mb_start']} -> {result['server_mem_mb_end']} MB   "
        f"backlog at end: {result['scheduler_backlog_end']}"
    )


def loadtest(
    phones: str = typer.Option(
        "1,2,3,5", help="Comma-separated phone counts, run one after another."
    ),
    fps: float = typer.Option(10.0, min=0.5, max=60.0, help="Frames per second per phone."),
    source: Path = typer.Option(
        ..., exists=True, file_okay=False, help="Directory of JPEG frames."
    ),
    duration: float = typer.Option(30.0, min=1.0, help="Seconds per phone count."),
    server: str = typer.Option("https://localhost:8443", help="Server base URL."),
    admin_user: str = typer.Option("admin"),
    admin_password: str = typer.Option(..., prompt=True, hide_input=True),
    queue: str = typer.Option(
        "latest", help="latest | fifo: must match the server's SCHEDULER_STRATEGY."
    ),
    ca: Path | None = typer.Option(None, help="CA certificate (mkcert rootCA.pem) for https."),
    insecure: bool = typer.Option(False, help="Skip TLS verification (local testing only)."),
    out: Path | None = typer.Option(None, help="Write results as JSON to this file."),
) -> None:
    """Stream JPEGs from N fake phones against a live server and report per-camera metrics."""
    if queue not in ("latest", "fifo"):
        raise typer.BadParameter("queue must be latest or fifo")
    counts = [int(p) for p in phones.split(",") if p.strip()]
    images = load_images(source)
    ssl_ctx: ssl.SSLContext | None = None
    verify: ssl.SSLContext | bool = True
    if server.startswith("https://"):
        if insecure:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            verify = False
        else:
            ssl_ctx = ssl.create_default_context(cafile=str(ca) if ca else None)
            verify = ssl_ctx

    async def main() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(
            base_url=server, verify=verify, headers=CSRF, timeout=30
        ) as client:
            r = await client.post(
                "/api/auth/login", json={"username": admin_user, "password": admin_password}
            )
            if r.status_code != 204:
                raise LoadTestError(f"login failed: {r.status_code} {r.text}")
            # The cookie is Secure; send it by hand so plain-http test servers work too.
            match = re.search(r"ps_admin=([^;]+)", r.headers.get("set-cookie", ""))
            if match is None:
                raise LoadTestError("login did not set the ps_admin cookie")
            client.cookies.clear()
            client.headers["cookie"] = f"ps_admin={match.group(1)}"
            strategy = (await client.get("/api/metrics")).json()["server"]["scheduler_strategy"]
            if strategy != queue:
                raise LoadTestError(
                    f"server runs SCHEDULER_STRATEGY={strategy} but --queue {queue} was requested. "
                    f"Restart it with SCHEDULER_STRATEGY={queue} (e.g. in .env) and run again."
                )
            results = []
            for n in counts:
                typer.echo(f"running {n} phone(s)...")
                result = await run_one(client, server, n, fps, images, duration, ssl_ctx)
                print_table(result)
                results.append(result)
            return results

    try:
        results = asyncio.run(main())
    except LoadTestError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from None
    if out is not None:
        out.write_text(json.dumps(results, indent=2))
        typer.echo(f"wrote {out}")


# --- publicsafe eval ... (M5) ---------------------------------------------------------

eval_app = typer.Typer(
    help="Offline evaluation on MOT17 and recordings (spec I).", no_args_is_help=True
)


def _eval_fail(message: str, code: int = 2) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


@eval_app.command("fetch")
def eval_fetch(
    dataset: str = typer.Argument(..., help="Dataset name; only `mot17` is supported."),
    dest: Path = typer.Option(Path("data/mot17"), help="Target directory (git-ignored)."),
    sequences: str = typer.Option(
        "MOT17-02,MOT17-04,MOT17-09", help="Comma-separated train sequences."
    ),
    archive: Path | None = typer.Option(
        None, exists=True, dir_okay=False, help="Use an already downloaded MOT17.zip."
    ),
    workers: int = typer.Option(4, min=1, max=16, help="Parallel range requests."),
) -> None:
    """Download only the needed MOT17 sequences from the official archive and verify them."""
    from publicsafe.config import ROOT_DIR
    from publicsafe.eval.datasets import DatasetError, fetch_mot17

    if dataset.lower() != "mot17":
        raise _eval_fail(f"unknown dataset {dataset!r}; only mot17 is supported")
    target = dest if dest.is_absolute() else ROOT_DIR / dest
    names = [s.strip() for s in sequences.split(",") if s.strip()]
    typer.echo("MOT17 is CC BY-NC-SA 3.0 (https://motchallenge.net/); never commit it.")
    try:
        results = fetch_mot17(target, names, archive=archive, workers=workers, progress=typer.echo)
    except DatasetError as exc:
        raise _eval_fail(str(exc), 3) from None
    for r in results:
        state = "verified" if r.verified else "NOT RECORDED - add it to MOT17_MANIFEST_SHA256"
        typer.echo(
            f"{r.sequence}: {r.files} files, {r.bytes / 1e6:.1f} MB, "
            f"manifest {r.manifest_sha256} ({state})"
        )


@eval_app.command("derive-gt")
def eval_derive_gt(
    sequence: Path = typer.Option(..., exists=True, file_okay=False, help="MOT sequence dir."),
    zone_file: Path = typer.Option(..., exists=True, dir_okay=False),
    out_dir: Path = typer.Option(Path("data/gt/derived"), help="Where to write the CSVs."),
    gt_classes: str = typer.Option("1", help="MOT classes counted as persons."),
    min_visibility: float = typer.Option(0.25, min=0.0, max=1.0),
    every: int = typer.Option(1, min=1, help="Label every k-th frame."),
) -> None:
    """Write ``source=derived`` in-zone GT (GT bottom-centre in the polygon); sanity checks only."""
    from publicsafe.config import ROOT_DIR
    from publicsafe.eval.datasets import (
        append_gt_rows,
        derive_gt,
        load_zone_file,
        read_mot_gt,
        read_seqinfo,
    )

    info = read_seqinfo(sequence)
    zones = load_zone_file(zone_file)
    boxes = read_mot_gt(sequence / "gt" / "gt.txt")
    classes = [int(c) for c in gt_classes.split(",") if c.strip()]
    target = out_dir if out_dir.is_absolute() else ROOT_DIR / out_dir
    for zone in zones.zones:
        path = target / f"{zones.sequence}.{zone.name}.csv"
        path.unlink(missing_ok=True)
        rows = derive_gt(
            boxes, zone, info.width, info.height, info.length, classes, min_visibility, every
        )
        append_gt_rows(path, rows)
        inside = sum(r[2] for r in rows)
        typer.echo(f"wrote {path}: {len(rows)} rows, {inside} in zone (source=derived)")
    typer.secho(
        "Derived GT favours the foot-point rules; never use it for the headline result (I.3).",
        fg=typer.colors.YELLOW,
    )


@eval_app.command("run")
def eval_run(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="Experiment YAML (I.5a)."),
    sweep: list[str] = typer.Option(
        [], help="Hysteresis sweep, e.g. --sweep enter_frames=1,3,5 --sweep exit_frames=1,5,10"
    ),
    track: bool = typer.Option(False, help="Tracking (E4, P1) - not implemented."),
    run_id: str | None = typer.Option(None, help="Output folder name (default: time + hash)."),
    no_figures: bool = typer.Option(False, help="Skip matplotlib figures."),
) -> None:
    """Run detector + all rules + state machine; write results.csv, metrics.json, report.md."""
    from publicsafe.eval.datasets import DatasetError
    from publicsafe.eval.runner import ConfigError, InvariantError, parse_sweep, run

    if track:
        raise _eval_fail("--track (E4) is a P1 option that is not implemented yet", 2)
    try:
        settings = parse_sweep(sweep) if sweep else []
        out = run(
            config, sweep=settings, run_id=run_id, progress=typer.echo, figures=not no_figures
        )
    except (ConfigError, DatasetError) as exc:
        raise _eval_fail(str(exc), 2) from None
    except InvariantError as exc:
        raise _eval_fail(f"assert_invariants failed: {exc}", 4) from None
    pooled = [r for r in out.metrics["results"] if r["sequence"] == "ALL"]
    typer.echo(f"\n{'rule':<22}{'precision':>10}{'recall':>8}{'f1':>8}{'event_f1':>10}")
    for r in pooled:

        def f(v: Any) -> str:
            return "-" if v is None else f"{v:.3f}"

        typer.echo(
            f"{r['rule']:<22}{f(r['precision']):>10}{f(r['recall']):>8}{f(r['f1']):>8}"
            f"{f(r.get('event_f1')):>10}"
        )
    typer.echo(f"wrote {out.run_dir}")


@eval_app.command("check")
def eval_check(
    run_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="data/eval/<run_id>"),
) -> None:
    """Re-check the rule invariants (E2) on a stored per_frame.jsonl; exit 4 if any is violated."""
    from publicsafe.eval.runner import check_per_frame

    problems = check_per_frame(run_dir / "per_frame.jsonl")
    for p in problems[:20]:
        typer.echo(p)
    if problems:
        raise _eval_fail(f"{len(problems)} invariant problem(s)", 4)
    typer.echo("invariants hold on every frame")


@eval_app.command("annotate")
def eval_annotate(
    sequence: Path = typer.Option(..., exists=True, file_okay=False, help="MOT sequence dir."),
    zone_file: Path = typer.Option(..., exists=True, dir_okay=False),
    zone: str = typer.Option(..., help="Zone name inside the zone file."),
    annotator: str = typer.Option(..., help="Your name or initials (stored in every row)."),
    every: int = typer.Option(5, min=1, help="Show every k-th frame."),
    out: Path | None = typer.Option(None, help="Default: data/gt/<sequence>.<zone>.csv"),
    port: int = typer.Option(8765, min=1024, max=65535),
    gt_classes: str = typer.Option("1"),
    min_visibility: float = typer.Option(0.25, min=0.0, max=1.0),
) -> None:
    """Open a local page to label in_zone per GT person; appends source=manual rows."""
    from publicsafe.config import ROOT_DIR
    from publicsafe.eval.annotate import AnnotateSession, serve
    from publicsafe.eval.datasets import load_zone_file

    zones = load_zone_file(zone_file)
    target = out or ROOT_DIR / "data" / "gt" / f"{zones.sequence}.{zone}.csv"
    session = AnnotateSession(
        seq_dir=sequence,
        sequence=zones.sequence,
        zone=zones.zone(zone),
        out=target,
        annotator=annotator,
        every=every,
        gt_classes=tuple(int(c) for c in gt_classes.split(",") if c.strip()),
        min_visibility=min_visibility,
    )
    server = serve(session, port)
    typer.echo(f"annotating {zones.sequence} / {zone} into {target}")
    typer.echo(f"open http://127.0.0.1:{server.server_address[1]}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


@eval_app.command("agreement")
def eval_agreement(
    a: Path = typer.Argument(..., exists=True, dir_okay=False, help="First annotator's CSV."),
    b: Path = typer.Argument(..., exists=True, dir_okay=False, help="Second annotator's CSV."),
    show: int = typer.Option(50, help="How many disagreements to list."),
) -> None:
    """Cohen's kappa between two annotators on the (frame, person) pairs both labelled."""
    from publicsafe.eval.datasets import read_gt_csv
    from publicsafe.eval.metrics import cohens_kappa

    la, lb = read_gt_csv(a).labels, read_gt_csv(b).labels
    common = sorted(set(la) & set(lb))
    if not common:
        raise _eval_fail("the two files share no (frame, person_id) pairs")
    kappa = cohens_kappa([la[k] for k in common], [lb[k] for k in common])
    diff = [k for k in common if la[k] != lb[k]]
    typer.echo(
        f"items in common: {len(common)}, disagreements: {len(diff)}, Cohen's kappa: {kappa:.3f}"
    )
    for frame, pid in diff[:show]:
        typer.echo(
            f"  frame {frame} person {pid}: {a.name}={la[(frame, pid)]} {b.name}={lb[(frame, pid)]}"
        )
