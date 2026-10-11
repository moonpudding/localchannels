"""Browser playability checks and fix commands for indexed videos.

Everything here works from what the indexer already stored (container, codec
names, file extension), so classifying the whole library costs one query and
no disk access. ``container`` is the first entry of ffprobe's format_name
("mov", "matroska", "mpegts", "avi", ...), which is what the indexer keeps.
"""

from __future__ import annotations

import os
import shlex
from typing import Callable, Optional

# Containers no browser plays in a plain <video> element, whatever is inside.
UNSUPPORTED_CONTAINERS = {
    "avi": "AVI",
    "mpegts": "MPEG-TS",
    "flv": "FLV",
    "asf": "WMV/ASF",
    "mpeg": "MPEG-PS",
    "mxf": "MXF",
    "wtv": "WTV",
}

# Video codecs every current browser decodes in software.
VIDEO_OK = {"h264", "av1", "vp9", "vp8"}
# Plays only with hardware decoding support (Chrome on Linux/Windows needs a
# hardware HEVC decoder; Firefox on Linux decodes it through VA-API only).
VIDEO_LIMITED = {"hevc"}
# Audio codecs every current browser decodes.
AUDIO_OK = {"aac", "mp3", "opus", "vorbis", "flac"}
# Audio known to work in some browsers or containers only.
AUDIO_LIMITED_PREFIXES = ("pcm_",)

# Extensions that are honest for each container (first format_name entry).
CONTAINER_EXTS = {
    "mov": {".mp4", ".m4v", ".mov", ".3gp", ".3g2", ".m4a"},
    "matroska": {".mkv", ".webm", ".mka"},
    "avi": {".avi"},
    "mpegts": {".ts", ".m2ts", ".mts"},
    "mpeg": {".mpg", ".mpeg", ".vob"},
    "flv": {".flv"},
    "asf": {".wmv", ".asf"},
    "ogg": {".ogv", ".ogg"},
    "mxf": {".mxf"},
    "wtv": {".wtv"},
}
# The extension to rename to when a file's extension lies about its container.
CONTAINER_PREFERRED_EXT = {
    "mov": ".mp4", "matroska": ".mkv", "avi": ".avi", "mpegts": ".ts",
    "mpeg": ".mpg", "flv": ".flv", "asf": ".wmv", "ogg": ".ogv",
    "mxf": ".mxf", "wtv": ".wtv",
}

# Issue kinds, in the order the UI lists them.
ISSUE_LABELS = {
    "container": "Unsupported container",
    "video": "Unsupported video codec",
    "audio": "Unsupported audio codec",
    "hevc": "HEVC (needs hardware decoding)",
    "extension": "Extension doesn't match container",
}

_RANK = {"yes": 0, "limited": 1, "no": 2}


def ext_of(rel_path: str) -> str:
    return os.path.splitext(rel_path or "")[1].lower()


def ext_mismatch(rel_path: str, container: Optional[str]) -> bool:
    expected = CONTAINER_EXTS.get((container or "").lower())
    return bool(expected) and ext_of(rel_path) not in expected


def classify(rel_path: str, container: Optional[str], vcodec: Optional[str],
             acodec: Optional[str]) -> dict:
    """Playability verdict for one file.

    Returns {"playable": "yes"|"limited"|"no", "issues": [{"kind", "label",
    "detail", "severity"}]}. Files whose container was never probed (no
    metadata) come back as "yes" with no issues: there's nothing to judge.
    """
    c = (container or "").lower()
    v = (vcodec or "").lower()
    a = (acodec or "").lower()
    issues = []

    def add(kind, detail, severity):
        issues.append({"kind": kind, "label": ISSUE_LABELS[kind],
                       "detail": detail, "severity": severity})

    if c in UNSUPPORTED_CONTAINERS:
        add("container", f"{UNSUPPORTED_CONTAINERS[c]} files don't play in browsers", "no")
    if v and v not in VIDEO_OK:
        if v in VIDEO_LIMITED:
            add("hevc", "HEVC plays only where the browser has hardware HEVC decoding", "limited")
        else:
            add("video", f"{v.upper()} video doesn't decode in browsers", "no")
    if a and a not in AUDIO_OK:
        if a.startswith(AUDIO_LIMITED_PREFIXES):
            add("audio", f"{a.upper()} audio plays in some browsers only", "limited")
        else:
            add("audio", f"{a.upper()} audio doesn't decode in browsers", "no")
    if ext_mismatch(rel_path, c):
        add("extension", f"{ext_of(rel_path) or '(none)'} file is really "
                         f"{c or 'unknown'} (expected "
                         f"{', '.join(sorted(CONTAINER_EXTS[c]))})", "limited")

    playable = "yes"
    for i in issues:
        if _RANK[i["severity"]] > _RANK[playable]:
            playable = i["severity"]
    return {"playable": playable, "issues": issues}


def mp4_candidates(rel_path: str) -> list[str]:
    """Where a conversion of rel_path lands, in the order names are tried."""
    stem, ext = os.path.splitext(rel_path)
    first = f"{stem}-fixed.mp4" if ext.lower() == ".mp4" else f"{stem}.mp4"
    rest = [f"{stem}-fixed.mp4"] if first != f"{stem}-fixed.mp4" else []
    return [first, *rest, *(f"{stem}-fixed-{n}.mp4" for n in range(2, 10))]


def fix_command(rel_path: str, container: Optional[str], vcodec: Optional[str],
                acodec: Optional[str], issues: list[dict],
                exists: Optional[Callable[[str], bool]] = None) -> Optional[dict]:
    """Shell command that fixes a file, or None if nothing needs fixing.

    Paths are relative to the library root. A file whose only problem is a
    wrong extension is renamed; anything else is converted to MP4, copying
    the streams that are already browser-friendly (lossless, fast) and
    re-encoding the rest to H.264/AAC. Originals are left in place.

    ``exists(rel_path)`` lets the caller steer the output name around files
    already on disk: ffmpeg -n refuses to overwrite but still exits 0, so a
    collision would otherwise look like a successful conversion.
    """
    exists = exists or (lambda _p: False)
    kinds = {i["kind"] for i in issues}
    if not kinds:
        return None
    c = (container or "").lower()
    v = (vcodec or "").lower()
    a = (acodec or "").lower()
    stem, ext = os.path.splitext(rel_path)

    if kinds == {"extension"}:
        target = stem + CONTAINER_PREFERRED_EXT.get(c, ext)
        return {"kind": "rename", "output": target,
                "cmd": f"mv -n -- {shlex.quote(rel_path)} {shlex.quote(target)}"}

    output = next((o for o in mp4_candidates(rel_path) if not exists(o)),
                  mp4_candidates(rel_path)[-1])
    copy_v = not v or v in VIDEO_OK
    copy_a = not a or a in AUDIO_OK
    args = ["ffmpeg", "-n", "-hide_banner"]
    # AVI often lacks timestamps, which MP4 needs when the video is copied.
    if c == "avi" and copy_v:
        args += ["-fflags", "+genpts"]
    # Explicit maps: first video, every audio track; subtitles and data
    # streams (teletext, DVB subs, EPG in TS recordings) are dropped because
    # MP4 can't carry most of them and they would abort the conversion.
    args += ["-i", shlex.quote(rel_path), "-map", "0:v:0", "-map", "0:a?"]
    args += ["-c:v", "copy"] if copy_v else ["-c:v", "libx264", "-crf", "20", "-preset", "medium"]
    if a:
        args += ["-c:a", "copy"] if copy_a else ["-c:a", "aac", "-b:a", "192k"]
    args += ["-sn", "-dn", "-movflags", "+faststart", shlex.quote(output)]
    return {"kind": "remux" if copy_v and copy_a else "transcode",
            "output": output, "cmd": " ".join(args)}
