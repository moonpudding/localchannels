"""Filesystem indexing: walk the library, probe metadata, build thumbnails.

Uses ffprobe for metadata and ffmpeg for a single poster-frame thumbnail per
video. Both must be on PATH. Indexing is incremental: unchanged files (matching
mtime) are skipped, and DB rows for deleted files are pruned.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from .config import config
from . import db

# Simple module-level progress that the API can poll.
STATUS = {"running": False, "done": 0, "total": 0, "current": "",
          "finished_at": 0.0, "last": None}   # "last" = summary of the most recent scan

# Bracket styles that can delimit the tag section of a filename.
FILENAME_TAG_BRACKETS = {
    "square": ("[", "]"),
    "round": ("(", ")"),
    "curly": ("{", "}"),
}


def _tag_pattern(kind: str) -> str:
    open_c, close_c = FILENAME_TAG_BRACKETS.get(kind, FILENAME_TAG_BRACKETS["square"])
    return (
        re.escape(open_c)
        + r"([^" + re.escape(open_c) + re.escape(close_c) + r"]*)"
        + re.escape(close_c)
    )


def extract_filename_tags(filename: str, kind: str = "square") -> list[str]:
    """Pull space-delimited tags out of the bracketed section of a filename.

    e.g. "Channel - Title [TECH OC GUIDE].mp4" -> ["TECH", "OC", "GUIDE"]
    """
    tags: list[str] = []
    for section in re.findall(_tag_pattern(kind), filename):
        for token in section.split():
            token = token.strip()
            if token and token not in tags:
                tags.append(token)
    return tags


# Separators left dangling once a bracketed section is removed.
_DANGLING = " \t-–—_·|,;"


def strip_filename_tags(stem: str, kind: str = "square") -> str:
    """Remove the bracketed tag section from a filename stem.

    "Myvideo - VidTitle [TAG1 TAG2 TAG3]" -> "Myvideo - VidTitle"

    Only affects the display title; the file on disk is never touched. If
    stripping would leave nothing behind (a name that is *only* tags), the
    original stem is kept so the video doesn't end up nameless.
    """
    cleaned = re.sub(_tag_pattern(kind), " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(_DANGLING)
    return cleaned or stem.strip()


# Filename tokens that indicate VR / spherical content.
_VR_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(vr|180|360|sbs|tb|ou|lr|3dh|3dv|mono|mkx200|mkx220|"
    r"fisheye190|fisheye|rf52|vrca220)(?![a-z0-9])", re.IGNORECASE
)
_VR_POSITIVE = {"vr", "180", "360", "sbs", "tb", "ou", "3dh", "3dv",
                "mkx200", "mkx220", "fisheye190", "fisheye", "rf52", "vrca220"}


def detect_vr(filename: str) -> Optional[dict]:
    """Guess VR projection/layout from a filename, or None if it isn't VR."""
    found = {m.group(1).lower() for m in _VR_TOKEN_RE.finditer(filename)}
    if not (found & _VR_POSITIVE):
        return None
    if found & {"fisheye", "fisheye190", "mkx200", "mkx220", "rf52", "vrca220"}:
        projection = "fisheye"
    elif "360" in found:
        projection = "eq360"
    else:
        projection = "eq180"  # most VR is 180°
    if found & {"sbs", "lr", "3dh"}:
        layout = "sbs"
    elif found & {"tb", "ou", "3dv"}:
        layout = "tb"
    else:
        layout = "mono"
    return {"projection": projection, "layout": layout}


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def _parse_fps(rate: Optional[str]) -> Optional[float]:
    if not rate or rate in ("0/0", "N/A"):
        return None
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den = float(den)
            return round(float(num) / den, 3) if den else None
        return round(float(rate), 3)
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> Optional[dict]:
    """Return metadata dict for a video file, or None if ffprobe fails."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        result = _run(cmd)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = fmt.get("duration")
    try:
        duration = float(duration) if duration is not None else None
    except ValueError:
        duration = None
    if (duration is None or duration == 0) and vstream and vstream.get("duration"):
        try:
            duration = float(vstream["duration"])
        except ValueError:
            pass

    bitrate = fmt.get("bit_rate")
    try:
        bitrate = int(bitrate) if bitrate is not None else None
    except ValueError:
        bitrate = None

    fps = None
    if vstream:
        fps = _parse_fps(vstream.get("avg_frame_rate")) or _parse_fps(
            vstream.get("r_frame_rate")
        )

    return {
        "duration": duration,
        "width": vstream.get("width") if vstream else None,
        "height": vstream.get("height") if vstream else None,
        "fps": fps,
        "vcodec": vstream.get("codec_name") if vstream else None,
        "acodec": astream.get("codec_name") if astream else None,
        "bitrate": bitrate,
        "container": (fmt.get("format_name") or "").split(",")[0] or None,
    }


# Size of the head and tail slices read for the move-detection fingerprint.
QUICK_HASH_CHUNK = 512 * 1024


def quick_hash(path: Path, size: Optional[int] = None) -> Optional[str]:
    """Cheap content fingerprint: file size plus its first and last 512 KB.

    Enough to recognise a file that has been renamed or moved without reading
    gigabytes off disk. This is *not* a duplicate-detection hash — two distinct
    files could in principle collide — so it is only ever used to match a path
    that vanished against one that appeared.
    """
    try:
        if size is None:
            size = path.stat().st_size
        h = hashlib.sha1()
        h.update(str(size).encode("ascii"))
        with path.open("rb") as f:
            h.update(f.read(QUICK_HASH_CHUNK))
            if size > QUICK_HASH_CHUNK * 2:
                f.seek(-QUICK_HASH_CHUNK, os.SEEK_END)
                h.update(f.read(QUICK_HASH_CHUNK))
        return h.hexdigest()
    except OSError:
        return None


def thumb_name(rel_path: str) -> str:
    """Thumbnail filename for a library-relative path (content-addressed)."""
    digest = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.jpg"


_thumb_name = thumb_name   # legacy internal alias


def make_thumbnail(path: Path, rel_path: str, duration: Optional[float]) -> Optional[str]:
    name = _thumb_name(rel_path)
    out = config.thumbnail_dir / name
    if out.exists():
        return name
    seek = 1.0
    if duration and duration > 0:
        seek = max(0.0, duration * float(config.thumbnail_seek_fraction))
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{seek:.3f}", "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={int(config.thumbnail_width)}:-2",
        "-q:v", "3", str(out),
    ]
    try:
        result = _run(cmd)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return name if out.exists() and result.returncode == 0 else None


def _iter_videos():
    root = config.root_path
    if not root.exists():
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in config.video_extensions:
            yield p


def move_thumbnail(old_rel: str, new_rel: str, stored: Optional[str]) -> Optional[str]:
    """Follow a renamed file with its cached poster.

    Thumbnails are named from a hash of the path, so a rename would otherwise
    orphan them. Returns the value the `thumbnail` column should now hold: the
    new name if the move worked, the old one if the move failed but the file is
    still there, or None if there is no poster any more.
    """
    src_name = stored or thumb_name(old_rel)
    src = config.thumbnail_dir / src_name
    if not src.exists():
        return None
    new_name = thumb_name(new_rel)
    try:
        src.rename(config.thumbnail_dir / new_name)
        return new_name
    except OSError:
        return src_name


def _detect_moves(conn, entries: list, known: dict) -> int:
    """Reattach indexed videos whose files were renamed or moved.

    A file that disappears from one path and reappears at another is the same
    video, so it should keep its row — and with it the date it was added, its
    tags, favorite flag, watch history and resume point. Matching is by content
    fingerprint rather than filename, so it survives being renamed, moved to a
    different folder, or both at once.

    `entries` is (path, rel, stat) for everything found on disk; `known` is the
    index snapshot, and is updated in place so the caller sees the new paths.
    """
    present = {rel for _, rel, _ in entries}
    vanished = {rel: info for rel, info in known.items() if rel not in present}
    fresh = [e for e in entries if e[1] not in known]
    if not vanished or not fresh:
        return 0

    # Bucket the vanished rows by fingerprint. Rows indexed before this column
    # existed have no fingerprint and simply can't be matched; they get one on
    # this pass, so a later move will be caught.
    by_hash: dict[str, list] = {}
    sizes: set = set()
    for rel, info in vanished.items():
        if info["quick_hash"]:
            by_hash.setdefault(info["quick_hash"], []).append((rel, info))
            sizes.add(info["size"])
    if not by_hash:
        return 0

    moved = 0
    for path, rel, stat in fresh:
        # Size is baked into the fingerprint, so this prefilter avoids hashing
        # genuinely new files in the common case.
        if stat.st_size not in sizes:
            continue
        qh = quick_hash(path, stat.st_size)
        if not qh:
            continue
        bucket = by_hash.get(qh)
        if not bucket:
            continue
        old_rel, info = bucket.pop(0)
        if not bucket:
            del by_hash[qh]

        thumb = move_thumbnail(old_rel, rel, info["thumbnail"])
        db.move_video(conn, info["id"], rel, path.name, thumb)
        # Let the main loop treat this as a known file: an untouched mtime then
        # means no re-probe and no thumbnail rebuild.
        known[rel] = {**info, "quick_hash": qh}
        known.pop(old_rel, None)
        moved += 1
    return moved


def reindex(force: bool = False) -> dict:
    """Walk the library and update the database. Returns a summary."""
    STATUS.update(running=True, done=0, total=0, current="Scanning...", finished_at=0.0)
    root = config.root_path
    files = list(_iter_videos())
    STATUS["total"] = len(files)

    added = updated = skipped = 0
    with db.get_db() as conn:
        known = db.index_snapshot(conn)
        ignored = db.ignored_set(conn)
        auto_tags = db.get_setting(conn, "filename_tags_auto", "0") == "1"
        bracket_kind = db.get_setting(conn, "filename_tag_brackets", "square")

        # Stat everything up front; cheap, and move detection needs the full
        # picture of what is present before deciding what is genuinely new.
        entries = []
        for path in files:
            rel = str(path.relative_to(root))
            if rel in ignored:
                # Removed-from-library files stay on disk but out of the DB.
                STATUS["done"] += 1
                continue
            try:
                stat = path.stat()
            except OSError:
                STATUS["done"] += 1
                continue
            entries.append((path, rel, stat))

        STATUS["current"] = "Checking for renamed or moved files..."
        moved = _detect_moves(conn, entries, known)

        present_rel = []
        for path, rel, stat in entries:
            present_rel.append(rel)
            STATUS["current"] = rel
            mtime = stat.st_mtime
            info = known.get(rel)
            if not force and info and abs(info["mtime"] - mtime) < 1.0:
                # Unchanged. Self-heal a missing fingerprint (an unreadable file
                # on an earlier scan) so a future rename is still recognised.
                if not info["quick_hash"]:
                    qh = quick_hash(path, stat.st_size)
                    if qh:
                        db.set_quick_hash(conn, info["id"], qh)
                skipped += 1
                STATUS["done"] += 1
                continue

            meta = probe(path) or {}
            thumb = make_thumbnail(path, rel, meta.get("duration"))
            folder = str(Path(rel).parent)
            folder = "" if folder == "." else folder
            row = {
                "rel_path": rel,
                "folder": folder,
                "filename": path.name,
                "size": stat.st_size,
                "duration": meta.get("duration"),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "fps": meta.get("fps"),
                "vcodec": meta.get("vcodec"),
                "acodec": meta.get("acodec"),
                "bitrate": meta.get("bitrate"),
                "container": meta.get("container"),
                "thumbnail": thumb,
                "quick_hash": quick_hash(path, stat.st_size),
                "mtime": mtime,
                "indexed_at": time.time(),
                "added_at": time.time(),  # only applied on first insert
            }
            db.upsert_video(conn, row)
            vrow = db.video_by_rel(conn, rel)
            if vrow:
                if auto_tags:
                    for t in extract_filename_tags(path.name, bracket_kind):
                        db.add_tag(conn, vrow["id"], t)
                if not vrow["vr_manual"]:
                    vr = detect_vr(path.name)
                    if vr:
                        db.set_vr(conn, vrow["id"], True, vr["projection"], vr["layout"])
                    elif vrow["vr"]:
                        db.set_vr(conn, vrow["id"], False, None, None)
            if info:
                updated += 1
            else:
                added += 1
            STATUS["done"] += 1

        removed = db.delete_missing(conn, present_rel)

    summary = {
        "added": added,
        "updated": updated,
        "moved": moved,
        "skipped": skipped,
        "removed": removed,
        "total": len(files),
    }
    STATUS.update(running=False, current="", finished_at=time.time(), last=summary)
    return summary
