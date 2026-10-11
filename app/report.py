"""Media reports: CSV exports and the browser-compatibility listing.

A report covers the whole library or one folder (optionally with its
subfolders). The quick report comes straight from the index. The detailed one
also re-runs ffprobe on every file for what the index doesn't keep (track
counts, audio languages, embedded title/artist/... tags, chapters), probing a
few files at a time and streaming rows out as they finish so a big library
doesn't hit a request timeout.
"""

from __future__ import annotations

import csv
import io
import json
import shlex
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from . import compat
from .config import config

PROBE_WORKERS = 4

QUICK_FIELDS = [
    "path", "folder", "filename", "extension", "container", "ext_mismatch",
    "expected_extensions", "video_codec", "audio_codec", "width", "height",
    "resolution", "fps", "duration_s", "duration", "bitrate", "size_bytes",
    "size", "browser_playable", "compat_issues", "fix_command", "tags",
    "favorite", "watched", "play_count", "last_played", "added_at", "modified",
]
DETAIL_FIELDS = [
    "format_long_name", "video_tracks", "audio_tracks", "subtitle_tracks",
    "audio_languages", "subtitle_languages", "title", "artist", "album",
    "comment", "copyright", "encoder", "creation_time", "chapter_count",
    "chapters", "container_tags", "probe_error",
]


# ── scope ──────────────────────────────────────────────────────────────────

def scope_clause(folder: str, recursive: bool) -> tuple[str, list]:
    """WHERE clause selecting a folder (and optionally its subfolders)."""
    f = (folder or "").strip("/")
    if not f:
        return ("", []) if recursive else (" WHERE v.folder = ''", [])
    if recursive:
        return " WHERE (v.folder = ? OR v.folder LIKE ?)", [f, f"{f}/%"]
    return " WHERE v.folder = ?", [f]


def scoped_videos(conn: sqlite3.Connection, folder: str, recursive: bool) -> list[sqlite3.Row]:
    clause, params = scope_clause(folder, recursive)
    return conn.execute(
        f"SELECT v.* FROM videos v{clause} "
        "ORDER BY v.folder COLLATE NOCASE, v.filename COLLATE NOCASE",
        params,
    ).fetchall()


# ── compatibility listing ─────────────────────────────────────────────────

def _on_disk(rel_path: str) -> bool:
    return (config.root_path / rel_path).exists()


def _playable_library_paths(conn: sqlite3.Connection) -> set[str]:
    """Every indexed file that plays in browsers as-is."""
    return {
        r["rel_path"]
        for r in conn.execute("SELECT rel_path, container, vcodec, acodec FROM videos")
        if not compat.classify(r["rel_path"], r["container"], r["vcodec"], r["acodec"])["issues"]
    }


def incompatible(conn: sqlite3.Connection, folder: str, recursive: bool) -> dict:
    """Files with at least one browser-playability issue, plus per-kind counts.

    A file whose converted MP4 is already in the library (the fix script ran
    and the library was rescanned) is reported with ``converted`` set and no
    fix, so the list doubles as a checklist of originals safe to delete.
    """
    items, counts = [], {k: 0 for k in compat.ISSUE_LABELS}
    rows = scoped_videos(conn, folder, recursive)
    playable = _playable_library_paths(conn)
    for r in rows:
        verdict = compat.classify(r["rel_path"], r["container"], r["vcodec"], r["acodec"])
        if not verdict["issues"]:
            continue
        for i in verdict["issues"]:
            counts[i["kind"]] += 1
        converted = None
        if {i["kind"] for i in verdict["issues"]} != {"extension"}:
            converted = next((c for c in compat.mp4_candidates(r["rel_path"])
                              if c in playable), None)
        items.append({
            "id": r["id"], "rel_path": r["rel_path"], "folder": r["folder"],
            "filename": r["filename"], "container": r["container"],
            "vcodec": r["vcodec"], "acodec": r["acodec"], "size": r["size"],
            "duration": r["duration"], "width": r["width"], "height": r["height"],
            "has_thumb": bool(r["thumbnail"]),
            "playable": verdict["playable"], "issues": verdict["issues"],
            "converted": converted,
            "fix": None if converted else compat.fix_command(
                r["rel_path"], r["container"], r["vcodec"], r["acodec"],
                verdict["issues"], exists=_on_disk),
        })
    return {
        "items": items, "counts": counts, "labels": compat.ISSUE_LABELS,
        "scanned": len(rows),
        "converted": sum(1 for i in items if i["converted"]),
        "unplayable": sum(1 for i in items if i["playable"] == "no"),
        "limited": sum(1 for i in items if i["playable"] == "limited"),
    }


def fix_script(items: list[dict], scope_label: str) -> str:
    """A bash script running every fix command, for the host's library root."""
    out = [
        "#!/usr/bin/env bash",
        f"# LocalChannels fix script: {scope_label}",
        f"# Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}, {len(items)} file(s).",
        "#",
        "# Run it from your library root (the folder mounted at "
        f"{config.root_path} in LocalChannels),",
        "# or pass that folder as the first argument:  bash fix.sh /path/to/videos",
        "#",
        "# Files are converted to MP4 next to the original: streams browsers can",
        "# play are copied (lossless), the rest re-encoded to H.264/AAC. Files whose",
        "# only problem is a wrong extension are renamed. Nothing is overwritten",
        "# (a file whose output already exists is skipped) and originals are kept:",
        "# delete them yourself once you've checked the results, then rescan.",
        "set -u",
        'cd "${1:-.}" || exit 1',
        "ok=0; failed=0; skipped=0",
        "# fix OUTPUT COMMAND...  ffmpeg -n exits 0 even when it refuses to",
        "# overwrite, so check for the output here instead of trusting that.",
        'fix() { local out="$1"; shift',
        '  if [ -e "$out" ]; then skipped=$((skipped+1)); echo "SKIPPED (exists): $out" >&2',
        '  elif "$@" </dev/null && [ -e "$out" ]; then ok=$((ok+1))',
        '  else failed=$((failed+1)); echo "FAILED: $out" >&2; fi; }',
        "",
    ]
    for it in items:
        fix = it.get("fix")
        if not fix:
            continue
        out.append(f"# {', '.join(i['label'] for i in it['issues'])}")
        out.append(f"fix {shlex.quote(fix['output'])} {fix['cmd']}")
    out += ["", 'echo "Done: $ok fixed, $skipped skipped, $failed failed."', ""]
    return "\n".join(out)


# ── CSV ────────────────────────────────────────────────────────────────────

def _iso(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _hms(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    s = int(round(sec))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _human(n: Optional[int]) -> str:
    if n is None:
        return ""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def _bulk_maps(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """Tags and play stats for every video, in two queries instead of 2×N."""
    tags: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT vt.video_id, t.name FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
        "ORDER BY t.name COLLATE NOCASE"
    ):
        tags.setdefault(r["video_id"], []).append(r["name"])
    plays = {
        r["video_id"]: (r["n"], r["last"])
        for r in conn.execute(
            "SELECT video_id, COUNT(*) AS n, MAX(played_at) AS last "
            "FROM watch_history GROUP BY video_id"
        )
    }
    return tags, plays


def _quick_row(r: sqlite3.Row, tags: dict, plays: dict) -> dict:
    verdict = compat.classify(r["rel_path"], r["container"], r["vcodec"], r["acodec"])
    fix = compat.fix_command(r["rel_path"], r["container"], r["vcodec"], r["acodec"],
                             verdict["issues"])
    c = (r["container"] or "").lower()
    n_plays, last = plays.get(r["id"], (0, None))
    return {
        "path": r["rel_path"],
        "folder": r["folder"],
        "filename": r["filename"],
        "extension": compat.ext_of(r["rel_path"]),
        "container": r["container"] or "",
        "ext_mismatch": "yes" if compat.ext_mismatch(r["rel_path"], c) else "no",
        "expected_extensions": " ".join(sorted(compat.CONTAINER_EXTS.get(c, ()))),
        "video_codec": r["vcodec"] or "",
        "audio_codec": r["acodec"] or "",
        "width": r["width"] or "",
        "height": r["height"] or "",
        "resolution": f"{r['width']}x{r['height']}" if r["width"] and r["height"] else "",
        "fps": round(r["fps"], 3) if r["fps"] else "",
        "duration_s": round(r["duration"], 3) if r["duration"] is not None else "",
        "duration": _hms(r["duration"]),
        "bitrate": r["bitrate"] or "",
        "size_bytes": r["size"] if r["size"] is not None else "",
        "size": _human(r["size"]),
        "browser_playable": verdict["playable"],
        "compat_issues": "; ".join(i["detail"] for i in verdict["issues"]),
        "fix_command": fix["cmd"] if fix else "",
        "tags": " ".join(tags.get(r["id"], [])),
        "favorite": "yes" if r["favorite"] else "no",
        "watched": "yes" if r["watched"] else "no",
        "play_count": n_plays,
        "last_played": _iso(last),
        "added_at": _iso(r["added_at"]),
        "modified": _iso(r["mtime"]),
    }


def probe_details(path: Path) -> dict:
    """What the index doesn't store, straight from ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format",
           "-show_chapters", "-show_programs", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"probe_error": "ffprobe timed out"}
    except FileNotFoundError:
        return {"probe_error": "ffprobe not installed"}
    if proc.returncode != 0:
        msg = (proc.stderr or "").strip().splitlines()
        return {"probe_error": msg[-1] if msg else f"ffprobe exit {proc.returncode}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"probe_error": "unreadable ffprobe output"}

    streams = data.get("streams", [])
    fmt = data.get("format", {}) or {}
    tags = fmt.get("tags", {}) or {}
    lower = {k.lower(): v for k, v in tags.items()}
    # MPEG-TS keeps its title and channel in the program (service) tags.
    prog = next((p.get("tags") or {} for p in data.get("programs", []) if p.get("tags")), {})
    lower.setdefault("title", prog.get("service_name", ""))
    lower.setdefault("artist", prog.get("service_provider", ""))

    def by_type(t):
        return [s for s in streams if s.get("codec_type") == t]

    def langs(ss):
        return ",".join(sorted({(s.get("tags") or {}).get("language")
                                for s in ss} - {None, "", "und"}))

    # Cover art is stored as a one-frame video stream; don't count it as video.
    video = [s for s in by_type("video")
             if not (s.get("disposition") or {}).get("attached_pic")]
    chapters = [{"title": (c.get("tags") or {}).get("title"),
                 "start": c.get("start_time"), "end": c.get("end_time")}
                for c in data.get("chapters", [])]
    return {
        "format_long_name": fmt.get("format_long_name", ""),
        "video_tracks": len(video),
        "audio_tracks": len(by_type("audio")),
        "subtitle_tracks": len(by_type("subtitle")),
        "audio_languages": langs(by_type("audio")),
        "subtitle_languages": langs(by_type("subtitle")),
        "title": lower.get("title", ""),
        "artist": lower.get("artist", "") or lower.get("album_artist", ""),
        "album": lower.get("album", "") or lower.get("product", ""),
        "comment": lower.get("comment", "") or lower.get("description", ""),
        "copyright": lower.get("copyright", ""),
        "encoder": lower.get("encoder", "") or lower.get("software", "")
                   or lower.get("encoded_by", ""),
        "creation_time": lower.get("creation_time", ""),
        "chapter_count": len(chapters),
        "chapters": json.dumps(chapters, ensure_ascii=False) if chapters else "",
        "container_tags": json.dumps(tags, ensure_ascii=False, sort_keys=True) if tags else "",
        "probe_error": "",
    }


def _detail_for(rel_path: str) -> dict:
    path = (config.root_path / rel_path).resolve()
    if not str(path).startswith(str(config.root_path)) or not path.is_file():
        return {"probe_error": "file not found"}
    return probe_details(path)


def csv_stream(rows: list[sqlite3.Row], conn_maps: tuple[dict, dict],
               detailed: bool) -> Iterator[str]:
    """CSV text, a row at a time. Starts with a BOM so Excel reads UTF-8."""
    tags, plays = conn_maps
    fields = QUICK_FIELDS + (DETAIL_FIELDS if detailed else [])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")

    def flush() -> str:
        text = buf.getvalue()
        buf.seek(0)
        buf.truncate()
        return text

    writer.writeheader()
    yield "﻿" + flush()
    if not detailed:
        for r in rows:
            writer.writerow(_quick_row(r, tags, plays))
            yield flush()
        return
    # executor.map keeps the input order, so the CSV stays sorted by path
    # while several ffprobes run at once.
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        details = pool.map(_detail_for, [r["rel_path"] for r in rows])
        for r, d in zip(rows, details):
            writer.writerow({**_quick_row(r, tags, plays), **d})
            yield flush()


def filename_for(folder: str, recursive: bool, detailed: bool, only_issues: bool) -> str:
    scope = (folder or "").strip("/").replace("/", "_") or "library"
    bits = ["localchannels", "incompatible" if only_issues else "report", scope]
    if folder and not recursive:
        bits.append("flat")
    if detailed:
        bits.append("detailed")
    bits.append(datetime.now().strftime("%Y%m%d-%H%M"))
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in "-".join(bits))
    return safe + ".csv"
