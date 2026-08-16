"""SQLite storage layer for LocalChannels.

One connection per request via get_db(); the schema is created on first use.
Times are stored as unix epoch floats. Paths are stored relative to root_path
so the library survives being moved to a different machine.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from .config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id           INTEGER PRIMARY KEY,
    rel_path     TEXT UNIQUE NOT NULL,   -- path relative to root_path
    folder       TEXT NOT NULL,          -- parent folder relative to root ("" = root)
    filename     TEXT NOT NULL,
    size         INTEGER,
    duration     REAL,
    width        INTEGER,
    height       INTEGER,
    fps          REAL,
    vcodec       TEXT,
    acodec       TEXT,
    bitrate      INTEGER,
    container    TEXT,
    thumbnail    TEXT,                   -- filename inside thumbnail_dir
    quick_hash   TEXT,                   -- cheap content fingerprint, used to
                                         -- recognise renamed/moved files
    mtime        REAL,
    indexed_at   REAL,
    favorite     INTEGER NOT NULL DEFAULT 0,
    position     REAL NOT NULL DEFAULT 0,     -- resume point in seconds (0 = none)
    watched      INTEGER NOT NULL DEFAULT 0,
    added_at     REAL,                        -- when first indexed into the library
    vr           INTEGER NOT NULL DEFAULT 0,  -- 1 = VR / 360 / 180 content
    vr_projection TEXT,                       -- eq360 | eq180 | fisheye
    vr_layout    TEXT,                         -- mono | sbs | tb
    vr_manual    INTEGER NOT NULL DEFAULT 0    -- 1 = user set VR flags, don't auto-detect
);
CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder);
CREATE INDEX IF NOT EXISTS idx_videos_quick_hash ON videos(quick_hash);

CREATE TABLE IF NOT EXISTS watch_history (
    id        INTEGER PRIMARY KEY,
    video_id  INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    played_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_video ON watch_history(video_id);
CREATE INDEX IF NOT EXISTS idx_history_time ON watch_history(played_at);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS video_tags (
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (video_id, tag_id)
);

-- Tracks which videos have already been shown in a given channel session so
-- channel mode can shuffle-without-replacement and loop when exhausted.
CREATE TABLE IF NOT EXISTS channel_played (
    channel_key TEXT NOT NULL,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    played_at   REAL NOT NULL,
    PRIMARY KEY (channel_key, video_id)
);

-- User-defined named channels built from folders and/or tags.
CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    folders    TEXT NOT NULL DEFAULT '[]',   -- JSON array of folder paths
    tags       TEXT NOT NULL DEFAULT '[]',   -- JSON array of tag names
    created_at REAL
);

-- Server-side settings the UI can change (key/value strings).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Files the user removed from the library but kept on disk; the indexer skips
-- these so they don't reappear on the next scan.
CREATE TABLE IF NOT EXISTS ignored_files (
    rel_path   TEXT PRIMARY KEY,
    ignored_at REAL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migrations so existing databases pick up new columns."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(videos)").fetchall()]
    if "favorite" not in cols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    if "position" not in cols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN position REAL NOT NULL DEFAULT 0"
        )
    if "watched" not in cols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN watched INTEGER NOT NULL DEFAULT 0"
        )
    if "added_at" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN added_at REAL")
        # Backfill existing rows with their first-index time (or file mtime).
        conn.execute(
            "UPDATE videos SET added_at = COALESCE(indexed_at, mtime) "
            "WHERE added_at IS NULL"
        )
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN content_hash TEXT")
    if "hash_mtime" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN hash_mtime REAL")
    if "quick_hash" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN quick_hash TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_quick_hash ON videos(quick_hash)"
        )
    if "vr" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN vr INTEGER NOT NULL DEFAULT 0")
    if "vr_projection" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN vr_projection TEXT")
    if "vr_layout" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN vr_layout TEXT")
    if "vr_manual" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN vr_manual INTEGER NOT NULL DEFAULT 0")
    if "ai_summary" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN ai_summary TEXT")
    if "ai_summary_at" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN ai_summary_at REAL")
    if "ai_model" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN ai_model TEXT")


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Video helpers
# ---------------------------------------------------------------------------

def upsert_video(conn: sqlite3.Connection, meta: dict) -> None:
    cols = (
        "rel_path", "folder", "filename", "size", "duration", "width", "height",
        "fps", "vcodec", "acodec", "bitrate", "container", "thumbnail",
        "quick_hash", "mtime", "indexed_at", "added_at",
    )
    placeholders = ", ".join("?" for _ in cols)
    # added_at is set only on first insert; never overwritten on re-index.
    preserved = ("rel_path", "added_at")
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in preserved)
    sql = (
        f"INSERT INTO videos ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(rel_path) DO UPDATE SET {updates}"
    )
    conn.execute(sql, tuple(meta.get(c) for c in cols))


def move_video(conn: sqlite3.Connection, video_id: int, rel_path: str,
               filename: str, thumbnail: Optional[str]) -> None:
    """Repoint a row after its file was renamed or moved on disk.

    Keeps the same row id, so tags, favorites, watch history, resume position
    and the original added_at all survive. `thumbnail` is always written, so
    pass the poster's real filename (or None if there no longer is one) rather
    than leaving a stale name behind.
    """
    folder = str(Path(rel_path).parent)
    folder = "" if folder == "." else folder
    conn.execute(
        "UPDATE videos SET rel_path = ?, filename = ?, folder = ?, thumbnail = ? "
        "WHERE id = ?",
        (rel_path, filename, folder, thumbnail, video_id),
    )


def existing_index(conn: sqlite3.Connection) -> dict[str, float]:
    """Map of rel_path -> mtime for everything currently indexed."""
    rows = conn.execute("SELECT rel_path, mtime FROM videos").fetchall()
    return {r["rel_path"]: r["mtime"] for r in rows}


def index_snapshot(conn: sqlite3.Connection) -> dict[str, dict]:
    """Everything the scanner needs about indexed rows, keyed by rel_path."""
    rows = conn.execute(
        "SELECT id, rel_path, mtime, size, quick_hash, thumbnail FROM videos"
    ).fetchall()
    return {
        r["rel_path"]: {
            "id": r["id"], "mtime": r["mtime"], "size": r["size"],
            "quick_hash": r["quick_hash"], "thumbnail": r["thumbnail"],
        }
        for r in rows
    }


def set_quick_hash(conn: sqlite3.Connection, video_id: int, value: str) -> None:
    conn.execute("UPDATE videos SET quick_hash = ? WHERE id = ?", (value, video_id))


def video_by_rel(conn: sqlite3.Connection, rel_path: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM videos WHERE rel_path = ?", (rel_path,)
    ).fetchone()


def delete_missing(conn: sqlite3.Connection, present: Iterable[str]) -> int:
    present = set(present)
    rows = conn.execute("SELECT id, rel_path FROM videos").fetchall()
    gone = [r["id"] for r in rows if r["rel_path"] not in present]
    for vid in gone:
        conn.execute("DELETE FROM videos WHERE id = ?", (vid,))
    return len(gone)


def video_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    return d


def get_video(conn: sqlite3.Connection, video_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()


def last_played(conn: sqlite3.Connection, video_id: int) -> Optional[float]:
    row = conn.execute(
        "SELECT MAX(played_at) AS t FROM watch_history WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    return row["t"] if row and row["t"] is not None else None


def play_count(conn: sqlite3.Connection, video_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM watch_history WHERE video_id = ?", (video_id,)
    ).fetchone()
    return row["c"] if row else 0


def record_play(conn: sqlite3.Connection, video_id: int) -> None:
    conn.execute(
        "INSERT INTO watch_history (video_id, played_at) VALUES (?, ?)",
        (video_id, time.time()),
    )


def recent_history(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    # Most recent play per video, newest first, capped at `limit` distinct videos.
    return conn.execute(
        """
        SELECT v.*, h.played_at AS played_at
        FROM videos v
        JOIN (
            SELECT video_id, MAX(played_at) AS played_at
            FROM watch_history
            GROUP BY video_id
        ) h ON h.video_id = v.id
        ORDER BY h.played_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def set_favorite(conn: sqlite3.Connection, video_id: int, value: bool) -> None:
    conn.execute(
        "UPDATE videos SET favorite = ? WHERE id = ?",
        (1 if value else 0, video_id),
    )


def favorites(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM videos WHERE favorite = 1 ORDER BY filename"
    ).fetchall()


def set_watch_state(conn: sqlite3.Connection, video_id: int,
                    position: float, watched: bool) -> None:
    conn.execute(
        "UPDATE videos SET position = ?, watched = ? WHERE id = ?",
        (max(0.0, float(position)), 1 if watched else 0, video_id),
    )


def newest(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    # Newest by when the video was added to the library.
    return conn.execute(
        "SELECT * FROM videos ORDER BY (added_at IS NULL), added_at DESC, id DESC "
        "LIMIT ?", (limit,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def get_or_create_tag(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row["id"]


def tags_for_video(conn: sqlite3.Connection, video_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN video_tags vt ON vt.tag_id = t.id
        WHERE vt.video_id = ?
        ORDER BY t.name
        """,
        (video_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def add_tag(conn: sqlite3.Connection, video_id: int, name: str) -> None:
    tag_id = get_or_create_tag(conn, name)
    conn.execute(
        "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
        (video_id, tag_id),
    )


def bulk_add_tags(conn: sqlite3.Connection, video_ids: Iterable[int],
                  tag_names: Iterable[str]) -> None:
    for name in tag_names:
        name = name.strip()
        if not name:
            continue
        tag_id = get_or_create_tag(conn, name)
        for vid in video_ids:
            conn.execute(
                "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
                (vid, tag_id),
            )


def videos_for_tag(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT v.* FROM videos v
        JOIN video_tags vt ON vt.video_id = v.id
        JOIN tags t ON t.id = vt.tag_id
        WHERE LOWER(t.name) = LOWER(?)
        ORDER BY v.filename
        """,
        (name,),
    ).fetchall()


def untagged_videos(conn: sqlite3.Connection, order_by: str = "filename COLLATE NOCASE ASC") -> list[sqlite3.Row]:
    """Videos that carry no tags at all."""
    return conn.execute(
        f"""
        SELECT v.* FROM videos v
        WHERE NOT EXISTS (SELECT 1 FROM video_tags vt WHERE vt.video_id = v.id)
        ORDER BY {order_by}
        """
    ).fetchall()


def untagged_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM videos v "
        "WHERE NOT EXISTS (SELECT 1 FROM video_tags vt WHERE vt.video_id = v.id)"
    ).fetchone()[0]


def create_tag(conn: sqlite3.Connection, name: str) -> int:
    return get_or_create_tag(conn, name)


def rename_tag(conn: sqlite3.Connection, old: str, new: str) -> bool:
    new = new.strip()
    if not new:
        return False
    old_row = conn.execute("SELECT id FROM tags WHERE name = ?", (old,)).fetchone()
    if not old_row:
        return False
    existing = conn.execute(
        "SELECT id FROM tags WHERE name = ? AND id != ?", (new, old_row["id"])
    ).fetchone()
    if existing:
        # Merge into the existing tag: move associations, then drop the old tag.
        conn.execute(
            "UPDATE OR IGNORE video_tags SET tag_id = ? WHERE tag_id = ?",
            (existing["id"], old_row["id"]),
        )
        conn.execute("DELETE FROM video_tags WHERE tag_id = ?", (old_row["id"],))
        conn.execute("DELETE FROM tags WHERE id = ?", (old_row["id"],))
    else:
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, old_row["id"]))
    return True


def delete_tag(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM tags WHERE id = ?", (row["id"],))  # cascades video_tags
    return True


def delete_video(conn: sqlite3.Connection, video_id: int) -> None:
    # foreign_keys=ON cascades to watch_history, video_tags, channel_played.
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))


# ---------------------------------------------------------------------------
# Settings (key/value) + ignore list
# ---------------------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def all_settings(conn: sqlite3.Connection) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings").fetchall()}


def ignore_file(conn: sqlite3.Connection, rel_path: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ignored_files (rel_path, ignored_at) VALUES (?, ?)",
        (rel_path, time.time()),
    )


def ignored_set(conn: sqlite3.Connection) -> set:
    return {r["rel_path"] for r in conn.execute("SELECT rel_path FROM ignored_files").fetchall()}


def unignore_file(conn: sqlite3.Connection, rel_path: str) -> None:
    conn.execute("DELETE FROM ignored_files WHERE rel_path = ?", (rel_path,))


def set_content_hash(conn: sqlite3.Connection, video_id: int,
                     content_hash: str, mtime: float) -> None:
    conn.execute(
        "UPDATE videos SET content_hash = ?, hash_mtime = ? WHERE id = ?",
        (content_hash, mtime, video_id),
    )


def set_ai_summary(conn: sqlite3.Connection, video_id: int,
                   summary: Optional[str], model: Optional[str]) -> None:
    conn.execute(
        "UPDATE videos SET ai_summary = ?, ai_summary_at = ?, ai_model = ? "
        "WHERE id = ?",
        (summary, time.time() if summary else None, model, video_id),
    )


def set_vr(conn: sqlite3.Connection, video_id: int, vr: bool,
           projection: Optional[str], layout: Optional[str],
           manual: Optional[bool] = None) -> None:
    if manual is None:
        conn.execute(
            "UPDATE videos SET vr = ?, vr_projection = ?, vr_layout = ? WHERE id = ?",
            (1 if vr else 0, projection, layout, video_id),
        )
    else:
        conn.execute(
            "UPDATE videos SET vr = ?, vr_projection = ?, vr_layout = ?, vr_manual = ? "
            "WHERE id = ?",
            (1 if vr else 0, projection, layout, 1 if manual else 0, video_id),
        )


def vr_videos(conn: sqlite3.Connection, order_sql: str) -> list[sqlite3.Row]:
    return conn.execute(f"SELECT * FROM videos WHERE vr = 1 ORDER BY {order_sql}").fetchall()


def count_vr(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM videos WHERE vr = 1").fetchone()["c"]


def auto_vr_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Rows eligible for filename-based VR auto-detection (not manually set)."""
    return conn.execute("SELECT id, filename FROM videos WHERE vr_manual = 0").fetchall()


def remove_tag(conn: sqlite3.Connection, video_id: int, name: str) -> None:
    conn.execute(
        """
        DELETE FROM video_tags
        WHERE video_id = ?
          AND tag_id = (SELECT id FROM tags WHERE name = ?)
        """,
        (video_id, name),
    )


def all_tags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.name, COUNT(vt.video_id) AS count
        FROM tags t
        LEFT JOIN video_tags vt ON vt.tag_id = t.id
        GROUP BY t.id
        ORDER BY t.name
        """
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Saved channel helpers
# ---------------------------------------------------------------------------

def create_channel(conn: sqlite3.Connection, name: str,
                   folders: list, tags: list) -> int:
    cur = conn.execute(
        "INSERT INTO channels (name, folders, tags, created_at) VALUES (?, ?, ?, ?)",
        (name.strip(), json.dumps(folders), json.dumps(tags), time.time()),
    )
    return cur.lastrowid


def list_channels(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM channels ORDER BY name COLLATE NOCASE"
    ).fetchall()


def get_channel(conn: sqlite3.Connection, channel_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM channels WHERE id = ?", (channel_id,)
    ).fetchone()


def delete_channel(conn: sqlite3.Connection, channel_id: int) -> None:
    conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
