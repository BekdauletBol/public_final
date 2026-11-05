"""M5: ``publicsafe eval`` CLI smoke tests and the annotation page's HTTP API."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from publicsafe.cli import app
from publicsafe.eval.annotate import AnnotateSession, serve
from publicsafe.eval.datasets import ZoneSpec, append_gt_rows, read_gt_csv
from tests.cv.test_eval_synthetic import ZONE, build_run, build_sequence

runner = CliRunner()


def test_eval_help_lists_every_command() -> None:
    out = runner.invoke(app, ["eval", "--help"])
    assert out.exit_code == 0
    for name in ("fetch", "derive-gt", "run", "check", "annotate", "agreement"):
        assert name in out.output


def test_fetch_rejects_unknown_dataset() -> None:
    out = runner.invoke(app, ["eval", "fetch", "wildtrack"])
    assert out.exit_code == 2 and "only mot17" in out.output


def test_run_reports_missing_ground_truth(tmp_path: Path) -> None:
    config = build_run(tmp_path)
    (tmp_path / "SYN-01.band.csv").unlink()
    out = runner.invoke(app, ["eval", "run", "--config", str(config)])
    assert out.exit_code == 2
    assert "eval annotate" in out.output


def test_track_is_not_silently_ignored(tmp_path: Path) -> None:
    out = runner.invoke(app, ["eval", "run", "--config", str(build_run(tmp_path)), "--track"])
    assert out.exit_code == 2 and "not implemented" in out.output


def test_derive_gt_cli(tmp_path: Path) -> None:
    seq = build_sequence(tmp_path)
    zones = tmp_path / "z.json"
    zones.write_text(json.dumps({"sequence": "SYN-01", "frame_w": 200, "frame_h": 100,
                                 "zones": [{"name": "band", "points": ZONE}]}))  # fmt: skip
    out = runner.invoke(
        app,
        ["eval", "derive-gt", "--sequence", str(seq), "--zone-file", str(zones),
         "--out-dir", str(tmp_path / "gt")],
    )  # fmt: skip
    assert out.exit_code == 0, out.output
    gt = read_gt_csv(tmp_path / "gt" / "SYN-01.band.csv")
    assert gt.derived_only and len(gt.frames) == 100
    assert gt.labels[(1, 1)] == 1 and gt.labels[(1, 2)] == 0 and gt.labels[(60, 3)] == 0


def test_agreement_cli(tmp_path: Path) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    for path, who, labels in ((a, "A", (1, 0, 1, 0)), (b, "B", (1, 1, 1, 0))):
        keys = ((1, 1), (1, 2), (2, 1), (2, 2))
        append_gt_rows(
            path, [(f, p, v, "manual", who) for (f, p), v in zip(keys, labels, strict=True)]
        )
    out = runner.invoke(app, ["eval", "agreement", str(a), str(b)])
    assert out.exit_code == 0, out.output
    assert "disagreements: 1" in out.output and "kappa: 0.500" in out.output
    assert "frame 1 person 2" in out.output


@pytest.fixture
def annotate_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    seq = build_sequence(tmp_path)
    session = AnnotateSession(
        seq_dir=seq,
        sequence="SYN-01",
        zone=ZoneSpec("band", [tuple(p) for p in ZONE]),  # type: ignore[misc]
        out=tmp_path / "gt" / "SYN-01.band.csv",
        annotator="tester",
        every=10,
    )
    server = serve(session, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    assert host == "127.0.0.1"
    yield f"http://127.0.0.1:{port}", session
    server.shutdown()
    server.server_close()


def test_annotate_page_labels_and_appends(annotate_server) -> None:  # type: ignore[no-untyped-def]
    base, session = annotate_server
    with httpx.Client(base_url=base) as c:
        assert "annotate in_zone" in c.get("/").text
        state = c.get("/api/state").json()
        assert state["frames"][:3] == [1, 11, 21] and state["done"] == []
        frame = c.get("/api/frame/61").json()
        assert [p["person_id"] for p in frame["persons"]] == [
            1,
            2,
            3,
        ]  # the static person is ignored
        assert c.get("/img/61.jpg").headers["content-type"] == "image/jpeg"
        assert c.get("/img/62.jpg").status_code == 400  # not one of every-10th frames

        bad = c.post("/api/label", json={"frame": 61, "labels": [{"person_id": 1, "in_zone": 1}]})
        assert bad.status_code == 400 and "exactly persons" in bad.json()["error"]
        labels = [{"person_id": p, "in_zone": int(p == 1)} for p in (1, 2, 3)]
        assert c.post("/api/label", json={"frame": 61, "labels": labels}).json() == {
            "ok": True,
            "rows": 3,
        }
        assert c.get("/api/frame/61").json()["persons"][0]["in_zone"] == 1
        assert c.get("/api/state").json()["done"] == [61]
    gt = read_gt_csv(session.out)
    assert gt.labels == {(61, 1): 1, (61, 2): 0, (61, 3): 0}
    assert gt.sources == {"manual"} and gt.annotators == {"tester"}
