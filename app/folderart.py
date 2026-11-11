"""Folder artwork: folder.jpg/png for the library tiles, backdrop.jpg/png for
the page background.

The images are looked up live rather than indexed, so dropping a file into a
folder shows up on the next page load without a rescan. Lookups are cached per
directory and invalidated by the directory's mtime, which changes whenever a
file is added to or removed from it.

Tile images are downscaled once with ffmpeg and cached in the data directory —
a folder.jpg is often a multi-megabyte poster, and a library page can show a
hundred of them. Backdrops are served as they are: there is one per page, and
with blur turned off the full resolution is what you want.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from .config import config

# Filenames per kind, in order of preference. Matched case-insensitively.
ART_NAMES = {
    "folder": ("folder.jpg", "folder.jpeg", "folder.png"),
    "backdrop": ("backdrop.jpg", "backdrop.jpeg", "backdrop.png"),
}
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

TILE_WIDTH = 640           # px; tiles render ~230–400px wide, doubled for HiDPI
CACHE_DIR = config.data_dir / "folder-art"

_LOCK = threading.Lock()
_LOOKUPS: dict[str, tuple[int, dict]] = {}   # directory -> (mtime_ns, {kind: Path})
_FAILED: set[str] = set()                    # cache names ffmpeg couldn't produce


def _directory(rel_folder: str) -> Optional[Path]:
    """The folder on disk, or None if it's missing or escapes the library."""
    root = config.root_path.resolve()
    try:
        directory = (root / rel_folder).resolve()
    except OSError:
        return None
    if not directory.is_relative_to(root) or not directory.is_dir():
        return None
    return directory


def find(rel_folder: str) -> dict[str, Path]:
    """{kind: path} for the artwork present in a library-relative folder."""
    directory = _directory(rel_folder.strip("/"))
    if directory is None:
        return {}
    try:
        mtime = directory.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(directory)
    with _LOCK:
        cached = _LOOKUPS.get(key)
    if cached and cached[0] == mtime:
        return cached[1]

    wanted = {name for names in ART_NAMES.values() for name in names}
    present: dict[str, Path] = {}
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                lower = entry.name.lower()
                if lower in wanted and entry.is_file():
                    present.setdefault(lower, Path(entry.path))
    except OSError:
        return {}
    found = {}
    for kind, names in ART_NAMES.items():
        for name in names:
            if name in present:
                found[kind] = present[name]
                break
    with _LOCK:
        _LOOKUPS[key] = (mtime, found)
    return found


def url(rel_folder: str, kind: str) -> Optional[str]:
    """Browser URL for a folder's artwork, or None when it has none. The `v`
    parameter changes with the file, so the response can be cached hard."""
    path = find(rel_folder).get(kind)
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return (f"/api/folder-image?path={quote(rel_folder.strip('/'), safe='')}"
            f"&kind={kind}&v={st.st_mtime_ns:x}-{st.st_size:x}")


def tile_image(src: Path) -> Path:
    """A downscaled copy of `src` for the folder tiles, made on first use.
    Falls back to the original if ffmpeg is missing or can't read the file."""
    try:
        st = src.stat()
    except OSError:
        return src
    ext = ".png" if src.suffix.lower() == ".png" else ".jpg"   # PNG keeps its alpha
    prefix = hashlib.sha1(str(src).encode("utf-8")).hexdigest()[:16]
    name = f"{prefix}-{st.st_mtime_ns:x}-{st.st_size:x}{ext}"
    out = CACHE_DIR / name
    if out.exists():
        return out
    if name in _FAILED:
        return src

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f"tmp-{uuid.uuid4().hex}{ext}"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale='min({TILE_WIDTH},iw)':-2",
    ]
    if ext == ".jpg":
        cmd += ["-q:v", "3"]
    cmd.append(str(tmp))
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        ok = result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0
    except (subprocess.TimeoutExpired, OSError):
        ok = False
    if not ok:
        tmp.unlink(missing_ok=True)
        _FAILED.add(name)
        return src

    os.replace(tmp, out)
    # The source changed if an older copy exists under the same prefix.
    for stale in CACHE_DIR.glob(f"{prefix}-*"):
        if stale != out:
            stale.unlink(missing_ok=True)
    return out
