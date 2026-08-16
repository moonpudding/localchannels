"""Library backup: one zip file on disk, overwritten on every run.

Contains a consistent snapshot of the SQLite database (taken with sqlite3's
online backup API, so it's safe while the app is serving requests), the active
config.json, and optionally the cached thumbnails. Video files are never
touched — the library itself is the source of truth for those.

Only one backup is kept: `<data_dir>/backups/localchannels-backup.zip`. It is
built to a temporary file first and moved into place atomically, so a failed or
interrupted run never destroys the previous good backup.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import config, config_file_path
from . import db

FORMAT_VERSION = 1
BACKUP_DIR = config.data_dir / "backups"
BACKUP_NAME = "localchannels-backup.zip"
BACKUP_PATH = BACKUP_DIR / BACKUP_NAME

# Serialises concurrent backup requests so two runs can't fight over the file.
_LOCK = threading.Lock()

# Rows counted into the manifest, purely informational.
_COUNT_TABLES = ("videos", "tags", "video_tags", "watch_history", "channels", "settings")


def _snapshot_db(dest: Path) -> None:
    """Copy the live database to `dest` via SQLite's online backup API.

    Unlike copying the file, this is safe with WAL mode and concurrent writers,
    and the result is a self-contained single file (no -wal/-shm siblings).
    """
    src = sqlite3.connect(config.db_path, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _counts(path: Path) -> dict:
    counts = {}
    conn = sqlite3.connect(path)
    try:
        for table in _COUNT_TABLES:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                pass
    finally:
        conn.close()
    return counts


def _thumbnail_files() -> list[Path]:
    try:
        return sorted(p for p in config.thumbnail_dir.iterdir() if p.is_file())
    except OSError:
        return []


def include_thumbs_default() -> bool:
    with db.get_db() as conn:
        return db.get_setting(conn, "backup_include_thumbs", "0") == "1"


def create_backup(include_thumbnails: Optional[bool] = None) -> dict:
    """Build the backup, replacing any previous one. Returns status()."""
    if include_thumbnails is None:
        include_thumbnails = include_thumbs_default()

    with _LOCK:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        # Staging lives beside the final file so os.replace() stays atomic
        # (same filesystem) even when data_dir is a mounted volume.
        tmp_dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=BACKUP_DIR))
        tmp_zip = BACKUP_DIR / (BACKUP_NAME + ".part")
        try:
            snapshot = tmp_dir / "library.db"
            _snapshot_db(snapshot)

            cfg_file = config_file_path()
            cfg_included = cfg_file.is_file()
            thumbs = _thumbnail_files() if include_thumbnails else []

            manifest = {
                "format_version": FORMAT_VERSION,
                "app": "LocalChannels",
                "created_at": time.time(),
                "created_at_iso": datetime.now().astimezone().isoformat(timespec="seconds"),
                "site_name": config.site_name,
                "root_path": str(config.root_path),
                "data_dir": str(config.data_dir),
                "includes_config": cfg_included,
                "includes_thumbnails": bool(thumbs),
                "thumbnail_count": len(thumbs),
                "counts": _counts(snapshot),
            }

            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                z.writestr("manifest.json", json.dumps(manifest, indent=2))
                z.write(snapshot, "library.db")
                if cfg_included:
                    z.write(cfg_file, "config.json")
                for p in thumbs:
                    # JPEGs are already compressed; storing them is much faster.
                    z.write(p, f"thumbnails/{p.name}", compress_type=zipfile.ZIP_STORED)

            os.replace(tmp_zip, BACKUP_PATH)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            try:
                tmp_zip.unlink()
            except OSError:
                pass

    return status()


def read_manifest() -> Optional[dict]:
    try:
        with zipfile.ZipFile(BACKUP_PATH) as z:
            return json.loads(z.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def download_name(created_at: Optional[float] = None) -> str:
    """Timestamped name for the browser; the on-disk file keeps its fixed name."""
    stamp = datetime.fromtimestamp(created_at or time.time()).strftime("%Y%m%d-%H%M%S")
    return f"localchannels-backup-{stamp}.zip"


def status() -> dict:
    info = {
        "exists": False,
        "filename": BACKUP_NAME,
        "path": str(BACKUP_PATH),
        "size": 0,
        "created_at": None,
        "includes_thumbnails": False,
        "counts": {},
    }
    try:
        st = BACKUP_PATH.stat()
    except OSError:
        return info
    info.update(exists=True, size=st.st_size, created_at=st.st_mtime)
    manifest = read_manifest()
    if manifest:
        info["includes_thumbnails"] = bool(manifest.get("includes_thumbnails"))
        info["counts"] = manifest.get("counts", {})
        if manifest.get("created_at"):
            info["created_at"] = manifest["created_at"]
    return info
