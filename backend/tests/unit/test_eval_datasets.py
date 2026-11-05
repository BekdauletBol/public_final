"""M5: MOT17 fetch (range requests, offline via a mock transport), readers, zone and GT files."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from publicsafe.eval import datasets as ds

SEQINFO = (
    "[Sequence]\nname={name}\nimDir=img1\nframeRate=30\nseqLength=2\n"
    "imWidth=64\nimHeight=48\nimExt=.jpg\n"
)
GT = (
    "1,1,11,6,20,40,1,1,0.9\n2,1,12,6,20,40,1,1,0.8\n1,2,1,1,5,5,0,8,1.0\n2,3,30,10,10,30,1,1,0.1\n"
)


def fake_archive(sequence: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        base = f"MOT17/train/{sequence}-FRCNN/"
        zf.writestr(base, b"")
        zf.writestr(base + "seqinfo.ini", SEQINFO.format(name=sequence), zipfile.ZIP_DEFLATED)
        zf.writestr(base + "gt/gt.txt", GT, zipfile.ZIP_DEFLATED)
        zf.writestr(base + "det/det.txt", "not fetched", zipfile.ZIP_DEFLATED)
        zf.writestr(base + "img1/000001.jpg", b"\xff\xd8one", zipfile.ZIP_STORED)
        zf.writestr(base + "img1/000002.jpg", b"\xff\xd8two" * 100, zipfile.ZIP_DEFLATED)
        zf.writestr(f"MOT17/train/{sequence}-DPM/seqinfo.ini", "other copy")
    return buf.getvalue()


def range_client(blob: bytes) -> tuple[httpx.Client, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(blob))})
        start, end = (int(v) for v in request.headers["range"].removeprefix("bytes=").split("-"))
        seen.append(request.headers["range"])
        return httpx.Response(206, content=blob[start : end + 1])

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_fetch_by_range_requests_extracts_one_variant_without_det(tmp_path: Path) -> None:
    blob = fake_archive("MOT17-99")
    client, seen = range_client(blob)
    [result] = ds.fetch_mot17(
        tmp_path,
        ["MOT17-99"],
        url="https://example.test/MOT17.zip",
        client=client,
        expected_size=None,
    )
    seq = tmp_path / "train" / "MOT17-99"
    assert (seq / "img1" / "000002.jpg").read_bytes() == b"\xff\xd8two" * 100
    assert (seq / "seqinfo.ini").read_text().startswith("[Sequence]")
    assert not (seq / "det").exists()
    assert result.files == 4 and result.verified is None  # no recorded hash for a fake sequence
    assert (seq / "MANIFEST.sha256").read_text().count("\n") == 4
    assert seen, "members must be fetched with Range requests"

    seen.clear()  # a second fetch skips complete files
    ds.fetch_mot17(
        tmp_path, ["MOT17-99"], url="https://x.test/a.zip", client=client, expected_size=None
    )
    assert all(int(r.split("-")[1]) >= len(blob) - 70_000 for r in seen)  # only the directory


def test_fetch_rejects_a_changed_archive_and_a_wrong_manifest(tmp_path: Path) -> None:
    client, _ = range_client(fake_archive("MOT17-04"))
    with pytest.raises(ds.DatasetError, match="archive changed"):
        ds.fetch_mot17(tmp_path, ["MOT17-04"], url="https://x.test/a.zip", client=client)
    with pytest.raises(ds.DatasetError, match="does not match the recorded"):
        ds.fetch_mot17(
            tmp_path, ["MOT17-04"], url="https://x.test/a.zip", client=client, expected_size=None
        )


def test_fetch_from_a_local_archive(tmp_path: Path) -> None:
    archive = tmp_path / "MOT17.zip"
    archive.write_bytes(fake_archive("MOT17-98"))
    [result] = ds.fetch_mot17(tmp_path / "out", ["MOT17-98"], archive=archive)
    assert result.files == 4
    with pytest.raises(ds.DatasetError, match="not found"):
        ds.fetch_mot17(tmp_path / "out", ["MOT17-97"], archive=archive)


def test_mot_reader_and_split(tmp_path: Path) -> None:
    gt = tmp_path / "gt.txt"
    gt.write_text(GT)
    frames = ds.read_mot_gt(gt)
    first = frames[1][0]
    assert first.bbox == (10.0, 5.0, 30.0, 45.0)  # 1-based left/top -> 0-based x1, y1
    assert first.bottom_center == (20.0, 45.0)
    counted, ignored = ds.split_gt(frames[2], [1], 0.25)
    assert [b.person_id for b in counted] == [1]
    assert [b.person_id for b in ignored] == [3]  # visibility 0.1 < 0.25
    gt.write_text("1,1,2,3\n")
    with pytest.raises(ds.DatasetError, match="9 columns"):
        ds.read_mot_gt(gt)


def test_zone_file_validation(tmp_path: Path) -> None:
    path = tmp_path / "z.json"
    good = {"sequence": "S", "frame_w": 64, "frame_h": 48, "TODO": "owner",
            "zones": [{"name": "a", "points": [[0, 0], [1, 0], [1, 1]]}]}  # fmt: skip
    path.write_text(json.dumps(good))
    zf = ds.load_zone_file(path)
    assert zf.names == ["a"] and zf.todo == "owner"
    good["zones"].append({"name": "b", "points": [[0, 0], [1, 1], [1, 0], [0, 1]]})  # bow-tie
    path.write_text(json.dumps(good))
    with pytest.raises(ds.DatasetError, match="zone 'b'"):
        ds.load_zone_file(path)


def test_gt_csv_round_trip_with_empty_frame_marker(tmp_path: Path) -> None:
    path = tmp_path / "S.a.csv"
    ds.append_gt_rows(path, [(1, 4, 1, "manual", "bb"), (2, ds.NO_PERSON, 0, "manual", "bb")])
    ds.append_gt_rows(path, [(1, 4, 0, "manual", "bb")])  # re-label: last row wins
    gt = ds.read_gt_csv(path)
    assert gt.labels == {(1, 4): 0}
    assert gt.frames == [1, 2]
    assert not gt.derived_only
    path.write_text("frame,person,in_zone\n")
    with pytest.raises(ds.DatasetError, match="header"):
        ds.read_gt_csv(path)


def test_derive_gt_labels_every_frame(tmp_path: Path) -> None:
    gt = tmp_path / "gt.txt"
    gt.write_text(GT)
    zone = ds.ZoneSpec("left", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)])
    rows = ds.derive_gt(
        ds.read_mot_gt(gt), zone, 64, 48, n_frames=3, gt_classes=[1], min_visibility=0.25
    )
    assert rows == [
        (1, 1, 1, "derived", "derived:gt_bottom_center"),  # feet (20, 45) inside x <= 32
        (2, 1, 1, "derived", "derived:gt_bottom_center"),
        (3, ds.NO_PERSON, 0, "derived", "derived:gt_bottom_center"),
    ]


def test_open_source_kinds(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    _ok, buf = cv2.imencode(".jpg", np.zeros((48, 64, 3), np.uint8))
    images = tmp_path / "imgs"
    images.mkdir()
    for i in range(3):
        (images / f"{i:03d}.jpg").write_bytes(buf.tobytes())
    src = ds.open_source(images, fps=10)
    assert src.kind == "images" and len(src) == 3
    assert [f.t_ms for f in src] == [0.0, 100.0, 200.0]

    rec = tmp_path / "rec"
    rec.mkdir()
    lines = []
    for seq, t in ((5, 1000.0), (6, 1066.0)):
        (rec / f"{seq:08d}.jpg").write_bytes(buf.tobytes())
        lines.append(json.dumps({"seq": seq, "t_phone_ms": t}))
    (rec / "frames.jsonl").write_text("\n".join(lines) + "\n")
    src = ds.open_source(rec)
    assert src.kind == "recording" and [f.t_ms for f in src] == [0.0, 66.0]

    video = tmp_path / "v.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
    for _ in range(4):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    src = ds.open_source(video)
    assert src.kind == "video" and src.fps == 25
    assert len(list(src)) == 4
