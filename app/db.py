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

-- The primary key already indexes (video_id, tag_id) for "tags of this video".
-- Filtering asks the opposite question — "videos carrying this tag" — which
-- would otherwise scan the whole join table once per tag in the filter.
CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);

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
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    folders      TEXT NOT NULL DEFAULT '[]',   -- JSON array of folder paths
    tags         TEXT NOT NULL DEFAULT '[]',   -- JSON array of tag names to include
    exclude_tags TEXT NOT NULL DEFAULT '[]',   -- JSON array of tag names to keep out
    tag_mode     TEXT NOT NULL DEFAULT 'any',  -- any | all, for the include list
    created_at   REAL
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

-- Duplicate groups the user has judged and dismissed (e.g. two cuts of the same
-- film with different soundtracks, which share a runtime on purpose). An ignore
-- is stored as the exact set of videos that were in the group at the time, not
-- as a fingerprint of the match, so it survives being found again by a
-- different method. See ignored_dupe_sets() for how the set is compared.
CREATE TABLE IF NOT EXISTS ignored_dupe_groups (
    id         INTEGER PRIMARY KEY,
    method     TEXT,                  -- how it was matched when dismissed (display only)
    ignored_at REAL
);

-- ON DELETE CASCADE is what keeps this table honest: delete a video and it
-- drops out of every ignore it was part of, so an ignore can never point at
-- files that are no longer in the library.
CREATE TABLE IF NOT EXISTS ignored_dupe_members (
    group_id INTEGER NOT NULL REFERENCES ignored_dupe_groups(id) ON DELETE CASCADE,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_ignored_dupe_video ON ignored_dupe_members(video_id);
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

    chan = [r["name"] for r in conn.execute("PRAGMA table_info(channels)").fetchall()]
    if chan and "exclude_tags" not in chan:
        conn.execute("ALTER TABLE channels ADD COLUMN exclude_tags TEXT "
                     "NOT NULL DEFAULT '[]'")
    if chan and "tag_mode" not in chan:
        conn.execute("ALTER TABLE channels ADD COLUMN tag_mode TEXT "
                     "NOT NULL DEFAULT 'any'")


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


def delete_missing(conn: sqlite3.Connection, present: Iterable[str]) -> list[Optional[str]]:
    """Drop rows whose files are no longer on disk.

    Returns one entry per removed row: its cached thumbnail name (or None), so
    the caller can delete the poster files too. len() of the result is the
    number of rows removed.
    """
    present = set(present)
    rows = conn.execute("SELECT id, rel_path, thumbnail FROM videos").fetchall()
    gone = [(r["id"], r["thumbnail"]) for r in rows if r["rel_path"] not in present]
    for vid, _ in gone:
        conn.execute("DELETE FROM videos WHERE id = ?", (vid,))
    return [thumb for _, thumb in gone]


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
# Library statistics
# ---------------------------------------------------------------------------

# Resolution classes for the quality breakdown, coarsest first. Compared on the
# larger dimension so portrait videos land in the right bucket, with a little
# slack below each nominal height (1072p encodes are 1080p in every sense).
_QUALITY_BUCKETS = (
    ("8K", 7000), ("4K", 3600), ("1440p", 2400), ("1080p", 1800),
    ("720p", 1200), ("480p", 800), ("SD", 1),
)


def _quality_bucket(width, height, vr) -> str:
    if vr:
        return "VR"
    longest = max(width or 0, height or 0)
    if not longest:
        return "Unknown"
    for label, threshold in _QUALITY_BUCKETS:
        if longest >= threshold:
            return label
    return "SD"


def _count_top(rows, key, limit: int, fields=("count",)) -> list[dict]:
    """Group rows by key, sum each field, return the top `limit` by count."""
    acc: dict = {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        slot = acc.setdefault(k, {f: 0 for f in fields})
        slot["count"] += 1
        for f in fields:
            if f != "count":
                slot[f] += r[f] or 0
    out = [{"name": k, **v} for k, v in acc.items()]
    out.sort(key=lambda d: (-d["count"], str(d["name"]).lower()))
    return out[:limit]


def library_stats(conn: sqlite3.Connection) -> dict:
    """Everything the Statistics page shows, in one pass over the library.

    Aggregation is done in Python over a compact projection of the videos
    table rather than in a dozen SQL queries; a library of tens of thousands of
    rows is a few megabytes and takes well under a second.
    """
    now = time.time()
    rows = conn.execute(
        "SELECT id, filename, folder, size, duration, width, height, fps, "
        "vcodec, container, bitrate, mtime, added_at, favorite, position, "
        "watched, vr, ai_summary IS NOT NULL AS has_ai FROM videos"
    ).fetchall()

    def top_by(field, reverse=True):
        """Row with the largest (or smallest) non-null value of a column."""
        cands = [r for r in rows if r[field] is not None and r[field] > 0]
        if not cands:
            return None
        r = max(cands, key=lambda x: x[field]) if reverse else \
            min(cands, key=lambda x: x[field])
        return {"id": r["id"], "filename": r["filename"], "value": r[field]}

    total = len(rows)
    watched = sum(1 for r in rows if r["watched"])
    in_progress = sum(1 for r in rows if not r["watched"] and (r["position"] or 0) > 0)
    dur_total = sum(r["duration"] or 0 for r in rows)
    dur_watched = sum(r["duration"] or 0 for r in rows if r["watched"])
    # What's left to watch: unwatched videos minus any partial progress.
    dur_remaining = sum(
        max(0.0, (r["duration"] or 0) - (r["position"] or 0))
        for r in rows if not r["watched"]
    )
    bytes_total = sum(r["size"] or 0 for r in rows)
    bitrates = [r["bitrate"] for r in rows if r["bitrate"]]

    # Quality breakdown, sorted by count so the biggest slice comes first.
    quality: dict[str, dict] = {}
    for r in rows:
        q = quality.setdefault(_quality_bucket(r["width"], r["height"], r["vr"]),
                               {"count": 0, "duration": 0.0, "bytes": 0})
        q["count"] += 1
        q["duration"] += r["duration"] or 0
        q["bytes"] += r["size"] or 0
    order = ["8K", "4K", "1440p", "1080p", "720p", "480p", "SD", "VR", "Unknown"]
    quality_list = [{"label": k, **quality[k]} for k in order if k in quality]

    # Plays. Time spent watching is estimated as one full runtime per recorded
    # play, which is how the history is stored (a play is logged, not timed).
    plays = conn.execute(
        "SELECT h.played_at, v.duration FROM watch_history h "
        "JOIN videos v ON v.id = h.video_id"
    ).fetchall()
    play_times = [p["played_at"] for p in plays]
    weekday = [0] * 7      # Monday = 0
    hour = [0] * 24
    for t in play_times:
        lt = time.localtime(t)
        weekday[lt.tm_wday] += 1
        hour[lt.tm_hour] += 1

    popular = conn.execute(
        "SELECT v.id, v.filename, v.duration, COUNT(h.id) AS plays, "
        "MAX(h.played_at) AS last_played "
        "FROM watch_history h JOIN videos v ON v.id = h.video_id "
        "GROUP BY v.id ORDER BY plays DESC, last_played DESC LIMIT 10"
    ).fetchall()

    tag_rows = conn.execute(
        "SELECT t.name, COUNT(vt.video_id) AS count FROM tags t "
        "JOIN video_tags vt ON vt.tag_id = t.id "
        "GROUP BY t.id ORDER BY count DESC, t.name COLLATE NOCASE LIMIT 10"
    ).fetchall()
    tag_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    tagged = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM video_tags"
    ).fetchone()[0]

    # Library growth: videos added per month over the last twelve months.
    growth: dict[str, int] = {}
    cutoff = now - 365 * 86400
    for r in rows:
        if r["added_at"] and r["added_at"] >= cutoff:
            lt = time.localtime(r["added_at"])
            key = f"{lt.tm_year:04d}-{lt.tm_mon:02d}"
            growth[key] = growth.get(key, 0) + 1

    def top_folders():
        # Top-level folder of each video, so the chart shows the library's
        # broad shape rather than every leaf directory.
        return _count_top(
            rows, lambda r: (r["folder"].split("/")[0] if r["folder"] else "(root)"),
            8, fields=("count", "size", "duration"),
        )

    return {
        "generated_at": now,
        "totals": {
            "videos": total,
            "watched": watched,
            "unwatched": total - watched,
            "in_progress": in_progress,
            "favorites": sum(1 for r in rows if r["favorite"]),
            "vr": sum(1 for r in rows if r["vr"]),
            "tagged": tagged,
            "untagged": total - tagged,
            "tags": tag_count,
            "ai_summarized": sum(1 for r in rows if r["has_ai"]),
            "folders": len({r["folder"] for r in rows}),
            "channels": conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
        },
        "runtime": {
            "total": dur_total,
            "watched": dur_watched,
            "remaining": dur_remaining,
            "average": dur_total / total if total else 0,
        },
        "storage": {
            "bytes": bytes_total,
            "average": bytes_total / total if total else 0,
            "avg_bitrate": sum(bitrates) / len(bitrates) if bitrates else 0,
        },
        "plays": {
            "total": len(play_times),
            "distinct": conn.execute(
                "SELECT COUNT(DISTINCT video_id) FROM watch_history").fetchone()[0],
            "time_watched": sum(p["duration"] or 0 for p in plays),
            "first": min(play_times) if play_times else None,
            "last": max(play_times) if play_times else None,
            "last_7d": sum(1 for t in play_times if t >= now - 7 * 86400),
            "last_30d": sum(1 for t in play_times if t >= now - 30 * 86400),
            "by_weekday": weekday,
            "by_hour": hour,
        },
        "quality": quality_list,
        "codecs": _count_top(rows, lambda r: r["vcodec"], 8),
        "containers": _count_top(rows, lambda r: r["container"], 8),
        "folders": top_folders(),
        "tags": [dict(t) for t in tag_rows],
        "popular": [dict(p) for p in popular],
        "growth": [{"month": k, "count": v} for k, v in sorted(growth.items())],
        "records": {
            "longest": top_by("duration"),
            "shortest": top_by("duration", reverse=False),
            "largest": top_by("size"),
            "highest_bitrate": top_by("bitrate"),
            "oldest_file": top_by("mtime", reverse=False),
            "newest_file": top_by("mtime"),
            "newest_added": top_by("added_at"),
        },
    }


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


def _filter_clause(
    *,
    any_tags: Optional[list[str]] = None,
    all_tags: Optional[list[str]] = None,
    not_tags: Optional[list[str]] = None,
    text: str = "",
    folder: str = "",
    untagged: bool = False,
) -> tuple[str, list]:
    """WHERE clause (over `videos v`) for a tag filter. Every clause is ANDed.

    any_tags  - carries at least one of these
    all_tags  - carries every one of these
    not_tags  - carries none of these
    text      - substring of the file path (case-insensitive)
    folder    - restricted to this folder and its subfolders
    untagged  - only videos with no tags at all

    Tag names are matched case-insensitively.
    """
    where: list[str] = []
    params: list = []

    def norm(names):
        seen, out = set(), []
        for n in names or []:
            n = (n or "").strip().lower()
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    any_t, all_t, not_t = norm(any_tags), norm(all_tags), norm(not_tags)

    if any_t:
        ph = ", ".join("?" for _ in any_t)
        where.append(
            f"EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
            f"WHERE vt.video_id = v.id AND LOWER(t.name) IN ({ph}))"
        )
        params.extend(any_t)

    if all_t:
        ph = ", ".join("?" for _ in all_t)
        where.append(
            f"(SELECT COUNT(DISTINCT LOWER(t.name)) FROM video_tags vt "
            f"JOIN tags t ON t.id = vt.tag_id "
            f"WHERE vt.video_id = v.id AND LOWER(t.name) IN ({ph})) = ?"
        )
        params.extend(all_t)
        params.append(len(all_t))

    if not_t:
        ph = ", ".join("?" for _ in not_t)
        where.append(
            f"NOT EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
            f"WHERE vt.video_id = v.id AND LOWER(t.name) IN ({ph}))"
        )
        params.extend(not_t)

    if untagged:
        where.append("NOT EXISTS (SELECT 1 FROM video_tags vt WHERE vt.video_id = v.id)")

    if text.strip():
        # Escape LIKE wildcards so a literal % or _ in a filename still matches.
        esc = (text.strip().lower()
               .replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
        where.append("LOWER(v.rel_path) LIKE ? ESCAPE '\\'")
        params.append(f"%{esc}%")

    if folder.strip("/"):
        f = folder.strip("/")
        where.append("(v.folder = ? OR v.folder LIKE ?)")
        params.extend([f, f"{f}/%"])

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return clause, params


def filter_videos(
    conn: sqlite3.Connection,
    *,
    order_by: str = "filename COLLATE NOCASE ASC",
    limit: Optional[int] = None,
    offset: int = 0,
    **filters,
) -> tuple[list[sqlite3.Row], int]:
    """Videos matching a tag filter; see _filter_clause for the clauses.

    Returns (rows, total) where total ignores limit/offset so the caller can
    paginate.
    """
    clause, params = _filter_clause(**filters)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM videos v{clause}", params
    ).fetchone()["n"]

    sql = f"SELECT v.* FROM videos v{clause} ORDER BY {order_by}"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [limit, max(0, offset)]
    return conn.execute(sql, params).fetchall(), total


def filter_tag_counts(conn: sqlite3.Connection, **filters) -> list[dict]:
    """How many of the videos matching a filter carry each tag.

    Counts cover the whole result set, not one page of it. Tags that no
    matching video carries are left out, which is what lets the filter UI
    offer only tags that still narrow the current results.
    """
    clause, params = _filter_clause(**filters)
    rows = conn.execute(
        f"""
        SELECT t.name, COUNT(*) AS count
        FROM video_tags vt
        JOIN tags t ON t.id = vt.tag_id
        WHERE vt.video_id IN (SELECT v.id FROM videos v{clause})
        GROUP BY t.id
        ORDER BY t.name
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


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


def delete_video(conn: sqlite3.Connection, video_id: int) -> Optional[str]:
    """Remove a row. Returns its cached thumbnail name so the caller can
    delete the poster file — the row is the only thing that references it."""
    row = conn.execute(
        "SELECT thumbnail FROM videos WHERE id = ?", (video_id,)
    ).fetchone()
    # foreign_keys=ON cascades to watch_history, video_tags, channel_played.
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    return row["thumbnail"] if row else None


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

def create_channel(conn: sqlite3.Connection, name: str, folders: list, tags: list,
                   exclude_tags: Optional[list] = None,
                   tag_mode: str = "any") -> int:
    cur = conn.execute(
        "INSERT INTO channels (name, folders, tags, exclude_tags, tag_mode, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name.strip(), json.dumps(folders), json.dumps(tags),
         json.dumps(exclude_tags or []),
         "all" if tag_mode == "all" else "any", time.time()),
    )
    return cur.lastrowid


def update_channel(conn: sqlite3.Connection, channel_id: int, name: str,
                   folders: list, tags: list, exclude_tags: list,
                   tag_mode: str) -> None:
    conn.execute(
        "UPDATE channels SET name = ?, folders = ?, tags = ?, exclude_tags = ?, "
        "tag_mode = ? WHERE id = ?",
        (name.strip(), json.dumps(folders), json.dumps(tags),
         json.dumps(exclude_tags or []),
         "all" if tag_mode == "all" else "any", channel_id),
    )


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


# ---------------------------------------------------------------------------
# Ignored duplicate groups
# ---------------------------------------------------------------------------
#
# Matching rule, in one sentence: a freshly-found group stays hidden while its
# members are a subset of a set the user already dismissed.
#
# That gives the two behaviours the feature needs, from one comparison:
#
#   * Delete one of three ignored copies and the remaining two are still a
#     subset of the dismissed set, so the group stays hidden — you don't get
#     asked again about a decision you already made.
#   * A fourth copy turns up and the group is no longer a subset of anything,
#     so it comes back for review. The superseded record is then deleted by
#     lift_dupe_ignore() rather than left to re-hide the group if that fourth
#     file later goes away.

def ignore_dupe_group(conn: sqlite3.Connection, video_ids: Iterable[int],
                      method: Optional[str] = None) -> int:
    """Dismiss a duplicate group. Returns the new ignore's id."""
    ids = sorted({int(v) for v in video_ids})
    cur = conn.execute(
        "INSERT INTO ignored_dupe_groups (method, ignored_at) VALUES (?, ?)",
        (method, time.time()),
    )
    gid = cur.lastrowid
    for vid in ids:
        conn.execute(
            "INSERT OR IGNORE INTO ignored_dupe_members (group_id, video_id) "
            "VALUES (?, ?)",
            (gid, vid),
        )
    return gid


def ignored_dupe_sets(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """Map of ignore id -> the video ids it covers.

    Records that can no longer describe a duplicate group are dropped on the
    way past: members vanish by cascade when their video leaves the library, so
    anything down to fewer than two members is dead weight.
    """
    sets: dict[int, set[int]] = {}
    rows = conn.execute(
        "SELECT g.id, m.video_id FROM ignored_dupe_groups g "
        "LEFT JOIN ignored_dupe_members m ON m.group_id = g.id"
    ).fetchall()
    for r in rows:
        sets.setdefault(r["id"], set())
        if r["video_id"] is not None:
            sets[r["id"]].add(r["video_id"])
    stale = [gid for gid, members in sets.items() if len(members) < 2]
    for gid in stale:
        conn.execute("DELETE FROM ignored_dupe_groups WHERE id = ?", (gid,))
        sets.pop(gid)
    return sets


def lift_dupe_ignore(conn: sqlite3.Connection, group_id: int) -> None:
    """Drop an ignore, either because the user asked or because it was outgrown."""
    conn.execute("DELETE FROM ignored_dupe_groups WHERE id = ?", (group_id,))


def ignored_dupe_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM ignored_dupe_groups"
    ).fetchone()["c"]
