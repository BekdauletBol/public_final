"""Evaluation datasets: MOT17 fetch and readers, zone files and in-zone ground truth (I.1-I.3).

MOT17 ground truth (``gt/gt.txt``) has 9 comma-separated columns
``frame, id, left, top, width, height, flag, class, visibility`` (1-based frame
and id). ``flag=0`` marks a row to ignore; ``class`` 1 is "pedestrian"; the other
classes are person on vehicle (2), car (3), bicycle (4), motorbike (5),
non-motorized vehicle (6), static person (7), distractor (8), occluder (9),
occluder on the ground (10), occluder full (11) and reflection (12). The MOT17
archive ships no README, so this was confirmed against the MOT16 benchmark paper
(Milan et al., arXiv:1603.00831, Table 2 and Section 3.4) and the
``seqinfo.ini`` / ``gt.txt`` files of the downloaded sequences.
"""

from __future__ import annotations

import configparser
import csv
import hashlib
import json
import struct
import time
import zipfile
import zlib
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np

from publicsafe.core.geometry import validate_polygon

# --- MOT17 archive ---------------------------------------------------------------

MOT17_URL = "https://motchallenge.net/data/MOT17.zip"
#: Identity of the official archive as served on 2026-11-05 (Content-Length, Last-Modified).
MOT17_ARCHIVE_SIZE = 5_860_214_001
MOT17_LAST_MODIFIED = "Fri, 18 Dec 2020 17:04:22 GMT"
MOT17_DEFAULT_SEQUENCES = ("MOT17-02", "MOT17-04", "MOT17-09")
#: Images and ``gt.txt`` are identical in the DPM, FRCNN and SDP copies; only ``det/`` differs.
MOT17_VARIANT = "FRCNN"
#: SHA-256 of each sequence's ``MANIFEST.sha256`` (one ``<sha256>  <path>`` line per file),
#: recorded after the first fetch on 2026-11-05. A sequence not listed here is fetched and
#: its manifest hash printed so it can be recorded.
MOT17_MANIFEST_SHA256: dict[str, str] = {
    "MOT17-02": "12c04bd52a35c39e8ff1a0c0e044979e911e682872b85e0147382d052de0c3f3",
    "MOT17-04": "ca02ae40d786dc6bb88f436fa28b820a1fb1840cecbddf91c0829df4d145d461",
    "MOT17-09": "d171da613f5e4973099e47f8250838f0106fed2072bdc92a51dafa9a33364ede",
}

_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_LOCAL_SIGNATURE = b"PK\x03\x04"
_HEADER_SLACK = 512


class DatasetError(RuntimeError):
    """A dataset file is missing, malformed or failed verification."""


class _HttpRangeFile:
    """Just enough of a seekable file over HTTP range requests for ``zipfile`` to list members."""

    def __init__(self, client: httpx.Client, url: str, size: int) -> None:
        self.client, self.url, self.size = client, url, size
        self.pos = 0

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        end = min(self.size, self.pos + n) - 1
        data = _get_range(self.client, self.url, self.pos, end)
        self.pos += len(data)
        return data


def _get_range(client: httpx.Client, url: str, start: int, end: int, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            r = client.get(url, headers={"Range": f"bytes={start}-{end}"})
            if r.status_code != 206:
                raise DatasetError(f"{url}: expected 206 Partial Content, got {r.status_code}")
            if len(r.content) != end - start + 1:
                raise httpx.ReadError("short read")
            return r.content
        except (httpx.TransportError, httpx.ReadError):
            if attempt == attempts:
                raise
            time.sleep(2.0 * attempt)
    raise AssertionError("unreachable")


def _member_bytes(client: httpx.Client, url: str, info: zipfile.ZipInfo) -> bytes:
    """Fetch one member with a single range request and check its CRC-32."""
    name_len = len(info.orig_filename.encode("utf-8"))
    want = _LOCAL_HEADER.size + name_len + _HEADER_SLACK + info.compress_size
    raw = _get_range(client, url, info.header_offset, info.header_offset + want - 1)
    sig, *_rest, fn_len, extra_len = _LOCAL_HEADER.unpack_from(raw)
    if sig != _LOCAL_SIGNATURE:
        raise DatasetError(f"{info.filename}: bad local header")
    start = _LOCAL_HEADER.size + fn_len + extra_len
    if start + info.compress_size > len(raw):  # unusually long extra field
        raw = _get_range(
            client, url, info.header_offset, info.header_offset + start + info.compress_size - 1
        )
    payload = raw[start : start + info.compress_size]
    if info.compress_type == zipfile.ZIP_STORED:
        data = payload
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        data = zlib.decompress(payload, -15)
    else:
        raise DatasetError(f"{info.filename}: unsupported compression {info.compress_type}")
    if zlib.crc32(data) != info.CRC:
        raise DatasetError(f"{info.filename}: CRC-32 mismatch")
    return data


def _sequence_members(
    infos: Sequence[zipfile.ZipInfo], sequence: str, variant: str
) -> list[tuple[zipfile.ZipInfo, str]]:
    """Members of ``train/<sequence>-<variant>/`` mapped to paths below ``<sequence>/``."""
    prefix = f"MOT17/train/{sequence}-{variant}/"
    out = []
    for info in infos:
        if info.filename.startswith(prefix) and not info.is_dir():
            rel = info.filename[len(prefix) :]
            if rel.startswith("det/"):
                continue  # public detections of one detector; not used
            out.append((info, rel))
    return out


def write_manifest(seq_dir: Path) -> str:
    """Write ``MANIFEST.sha256`` for the files below ``seq_dir``; returns the manifest's SHA-256."""
    lines = []
    for path in sorted(p for p in seq_dir.rglob("*") if p.is_file()):
        if path.name == "MANIFEST.sha256":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(seq_dir).as_posix()}\n")
    text = "".join(lines)
    (seq_dir / "MANIFEST.sha256").write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class FetchResult:
    sequence: str
    files: int
    bytes: int
    manifest_sha256: str
    verified: bool | None  # None = no recorded hash yet


def fetch_mot17(
    dest: Path,
    sequences: Sequence[str] = MOT17_DEFAULT_SEQUENCES,
    *,
    archive: Path | None = None,
    url: str = MOT17_URL,
    workers: int = 4,
    progress: Any = None,
    client: httpx.Client | None = None,
    expected_size: int | None = MOT17_ARCHIVE_SIZE,
) -> list[FetchResult]:
    """Extract the train sequences from the official MOT17 archive into ``dest/train/<seq>``.

    Without ``archive`` only the needed members are downloaded with HTTP range
    requests (about 450 MB for the three default sequences instead of 5.9 GB).
    Every member is checked against the archive's CRC-32, and the extracted
    sequence against :data:`MOT17_MANIFEST_SHA256` when a hash is recorded.
    """
    results: list[FetchResult] = []
    own_client = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        if archive is not None:
            zf = zipfile.ZipFile(archive)
            infos = zf.infolist()
        else:
            head = client.head(url)
            head.raise_for_status()
            size = int(head.headers["content-length"])
            if expected_size is not None and size != expected_size:
                raise DatasetError(
                    f"{url} is {size} bytes, expected {expected_size}; the archive changed. "
                    "Download it in full and pass --archive, then record the new values."
                )
            zf = zipfile.ZipFile(_HttpRangeFile(client, url, size))  # type: ignore[arg-type]
            infos = zf.infolist()

        for sequence in sequences:
            members = _sequence_members(infos, sequence, MOT17_VARIANT)
            if not members:
                raise DatasetError(f"{sequence} not found in the MOT17 train split")
            seq_dir = dest / "train" / sequence
            todo = [
                (info, rel)
                for info, rel in members
                if not (
                    (seq_dir / rel).is_file() and (seq_dir / rel).stat().st_size == info.file_size
                )
            ]

            def fetch_one(item: tuple[zipfile.ZipInfo, str], seq_dir: Path = seq_dir) -> int:
                info, rel = item
                data = zf.read(info) if archive is not None else _member_bytes(client, url, info)
                target = seq_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(data)
                tmp.replace(target)
                return len(data)

            done = 0
            with ThreadPoolExecutor(max_workers=max(1, workers if archive is None else 1)) as ex:
                for _ in ex.map(fetch_one, todo):
                    done += 1
                    if progress is not None and (done % 50 == 0 or done == len(todo)):
                        progress(f"{sequence}: {done}/{len(todo)} files")
            manifest = write_manifest(seq_dir)
            expected = MOT17_MANIFEST_SHA256.get(sequence)
            if expected is not None and expected != manifest:
                raise DatasetError(
                    f"{sequence}: manifest SHA-256 {manifest} does not match "
                    f"the recorded {expected}"
                )
            results.append(
                FetchResult(
                    sequence=sequence,
                    files=len(members),
                    bytes=sum(i.file_size for i, _ in members),
                    manifest_sha256=manifest,
                    verified=None if expected is None else True,
                )
            )
    finally:
        if own_client:
            client.close()
    return results


# --- MOT sequences -----------------------------------------------------------------


@dataclass(frozen=True)
class SeqInfo:
    name: str
    fps: float
    length: int
    width: int
    height: int


@dataclass(frozen=True)
class GtBox:
    """One row of a MOT ``gt.txt`` converted to ``x1, y1, x2, y2`` pixels."""

    frame: int
    person_id: int
    bbox: tuple[float, float, float, float]
    flag: int
    cls: int
    visibility: float

    @property
    def bottom_center(self) -> tuple[float, float]:
        x1, _y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, y2)


def read_seqinfo(seq_dir: Path) -> SeqInfo:
    parser = configparser.ConfigParser()
    if not parser.read(seq_dir / "seqinfo.ini"):
        raise DatasetError(f"{seq_dir}/seqinfo.ini not found")
    s = parser["Sequence"]
    return SeqInfo(
        name=s["name"],
        fps=float(s["frameRate"]),
        length=int(s["seqLength"]),
        width=int(s["imWidth"]),
        height=int(s["imHeight"]),
    )


def read_mot_gt(path: Path) -> dict[int, list[GtBox]]:
    """``gt.txt`` grouped by frame; validates the 9-column layout."""
    frames: dict[int, list[GtBox]] = {}
    with path.open(newline="") as fh:
        for lineno, row in enumerate(csv.reader(fh), start=1):
            if not row:
                continue
            if len(row) != 9:
                raise DatasetError(f"{path}:{lineno}: expected 9 columns, got {len(row)}")
            frame, pid = int(row[0]), int(row[1])
            left, top, w, h = (float(v) for v in row[2:6])
            box = GtBox(
                frame=frame,
                person_id=pid,
                bbox=(left - 1.0, top - 1.0, left - 1.0 + w, top - 1.0 + h),  # 1-based pixels
                flag=int(row[6]),
                cls=int(row[7]),
                visibility=float(row[8]),
            )
            frames.setdefault(frame, []).append(box)
    return frames


def split_gt(
    boxes: Sequence[GtBox], gt_classes: Sequence[int], min_visibility: float
) -> tuple[list[GtBox], list[GtBox]]:
    """(counted GT, ignore regions) per I.4: ``flag=0``, other classes and low visibility."""
    counted, ignored = [], []
    for b in boxes:
        if b.flag != 0 and b.cls in gt_classes and b.visibility >= min_visibility:
            counted.append(b)
        else:
            ignored.append(b)
    return counted, ignored


# --- frame sources -----------------------------------------------------------------

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


@dataclass
class SourceFrame:
    index: int  # 1-based, matches MOT frame numbers
    t_ms: float
    data: bytes | None  # encoded bytes when read from files (decode is timed by the runner)
    image: np.ndarray | None = None  # already decoded (video)


@dataclass
class FrameSource:
    """A video file, an image-sequence directory, a MOT sequence or an ``EVAL_RECORD`` directory."""

    path: Path
    fps: float
    kind: str
    files: list[Path] = field(default_factory=list)
    t_ms: list[float] = field(default_factory=list)
    width: int | None = None
    height: int | None = None

    def __len__(self) -> int:
        if self.kind == "video":
            cap = cv2.VideoCapture(str(self.path))
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            return n
        return len(self.files)

    def __iter__(self) -> Iterator[SourceFrame]:
        if self.kind == "video":
            cap = cv2.VideoCapture(str(self.path))
            index = 0
            try:
                while True:
                    ok, image = cap.read()
                    if not ok:
                        break
                    index += 1
                    yield SourceFrame(index, (index - 1) * 1000.0 / self.fps, None, image)
            finally:
                cap.release()
            return
        for i, path in enumerate(self.files):
            t = self.t_ms[i] if self.t_ms else i * 1000.0 / self.fps
            yield SourceFrame(i + 1, t, path.read_bytes())


def open_source(path: Path, fps: float | None = None) -> FrameSource:
    """Pick the reader from what ``path`` contains."""
    if path.is_file():
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise DatasetError(f"{path}: not a readable video")
        native = cap.get(cv2.CAP_PROP_FPS) or 0.0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        return FrameSource(path, fps or native or 30.0, "video", width=w, height=h)
    if not path.is_dir():
        raise DatasetError(f"{path} does not exist")
    if (path / "seqinfo.ini").is_file():
        info = read_seqinfo(path)
        files = sorted((path / "img1").glob("*.jpg"))
        if len(files) != info.length:
            raise DatasetError(f"{path}: {len(files)} images, seqinfo.ini says {info.length}")
        return FrameSource(
            path, fps or info.fps, "mot", files, width=info.width, height=info.height
        )
    if (path / "frames.jsonl").is_file():  # EVAL_RECORD output (H.7): keep t_phone_ms
        files, times = [], []
        for line in (path / "frames.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            jpg = path / f"{int(row['seq']):08d}.jpg"
            if jpg.is_file():
                files.append(jpg)
                times.append(float(row.get("t_phone_ms") or row.get("t_server_recv_ms") or 0.0))
        if times:
            t0 = times[0]
            times = [t - t0 for t in times]
        return FrameSource(path, fps or 10.0, "recording", files, times)
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise DatasetError(f"{path}: no images")
    return FrameSource(path, fps or 30.0, "images", files)


# --- zones and in-zone ground truth ------------------------------------------------


@dataclass(frozen=True)
class ZoneSpec:
    name: str
    points: list[tuple[float, float]]


@dataclass(frozen=True)
class ZoneFile:
    sequence: str
    frame_w: int
    frame_h: int
    zones: list[ZoneSpec]
    todo: str | None = None

    def zone(self, name: str) -> ZoneSpec:
        for z in self.zones:
            if z.name == name:
                return z
        raise DatasetError(f"zone {name!r} not in {self.sequence} (have {self.names})")

    @property
    def names(self) -> list[str]:
        return [z.name for z in self.zones]


def load_zone_file(path: Path) -> ZoneFile:
    """``data/zones/<sequence>.json`` (I.2); polygons are validated like the API does."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DatasetError(f"zone file {path} not found") from None
    zones = []
    for z in raw["zones"]:
        try:
            zones.append(ZoneSpec(str(z["name"]), validate_polygon(z["points"])))
        except ValueError as exc:
            raise DatasetError(f"{path}: zone {z.get('name')!r}: {exc}") from None
    names = [z.name for z in zones]
    if len(set(names)) != len(names):
        raise DatasetError(f"{path}: zone names must be unique")
    return ZoneFile(
        sequence=str(raw["sequence"]),
        frame_w=int(raw["frame_w"]),
        frame_h=int(raw["frame_h"]),
        zones=zones,
        todo=raw.get("TODO"),
    )


GT_COLUMNS = ("frame", "person_id", "in_zone", "source", "annotator")
GT_SOURCES = ("manual", "groundplane", "derived")


#: ``person_id`` of the marker row that says "this frame was annotated and nobody is in it".
NO_PERSON = 0


@dataclass
class ZoneGroundTruth:
    """``data/gt/<sequence>.<zone>.csv`` (I.3): the last row per (frame, person) wins.

    MOT ids start at 1, so a row with ``person_id=0`` only marks a frame as
    annotated (an empty frame would otherwise be indistinguishable from a
    skipped one).
    """

    labels: dict[tuple[int, int], int]
    annotated: set[int]
    sources: set[str]
    annotators: set[str]

    @property
    def frames(self) -> list[int]:
        return sorted(self.annotated)

    @property
    def derived_only(self) -> bool:
        return self.sources == {"derived"}


def read_gt_csv(path: Path) -> ZoneGroundTruth:
    labels: dict[tuple[int, int], int] = {}
    annotated: set[int] = set()
    sources: set[str] = set()
    annotators: set[str] = set()
    try:
        fh = path.open(newline="", encoding="utf-8")
    except FileNotFoundError:
        raise DatasetError(
            f"ground truth {path} not found; create it with `publicsafe eval annotate`"
        ) from None
    with fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != GT_COLUMNS:
            raise DatasetError(f"{path}: header must be {','.join(GT_COLUMNS)}")
        for row in reader:
            if row["source"] not in GT_SOURCES:
                raise DatasetError(f"{path}: unknown source {row['source']!r}")
            if row["in_zone"] not in ("0", "1"):
                raise DatasetError(f"{path}: in_zone must be 0 or 1")
            frame, pid = int(row["frame"]), int(row["person_id"])
            annotated.add(frame)
            if pid != NO_PERSON:
                labels[(frame, pid)] = int(row["in_zone"])
            sources.add(row["source"])
            annotators.add(row["annotator"])
    return ZoneGroundTruth(labels, annotated, sources, annotators)


def append_gt_rows(path: Path, rows: Sequence[tuple[int, int, int, str, str]]) -> None:
    """Append ``(frame, person_id, in_zone, source, annotator)`` rows, writing the header once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(GT_COLUMNS)
        writer.writerows(rows)


def derive_gt(
    gt: dict[int, list[GtBox]],
    zone: ZoneSpec,
    w: int,
    h: int,
    n_frames: int,
    gt_classes: Sequence[int],
    min_visibility: float,
    every: int = 1,
) -> list[tuple[int, int, int, str, str]]:
    """``source=derived`` rows: GT bottom-centre inside the polygon. Sanity checks only (I.3)."""
    from publicsafe.core.rules import ZonePolygon

    poly = ZonePolygon(zone.points, w, h)
    who = "derived:gt_bottom_center"
    rows = []
    for frame in range(1, n_frames + 1, every):
        counted, _ = split_gt(gt.get(frame, []), gt_classes, min_visibility)
        if not counted:
            rows.append((frame, NO_PERSON, 0, "derived", who))
        for b in sorted(counted, key=lambda b: b.person_id):
            inside = poly.covers_point(*b.bottom_center)
            rows.append((frame, b.person_id, int(inside), "derived", who))
    return rows
