"""LocalChannels — a self-hosted web video library and player.

Run with:  python -m app.main      (or)   uvicorn app.main:app --reload
"""
from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import os
import random
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .config import config
from . import about, ai, auth, backup, compat, db, folderart, indexer, report

@asynccontextmanager
async def _lifespan(app: "FastAPI"):
    # Startup: one-time VR auto-detection over the existing library, then kick
    # off a background index if the library looks empty.
    _backfill_vr()
    _maybe_initial_index()
    yield
    # Shutdown: nothing to clean up.


def _backfill_vr():
    with db.get_db() as conn:
        if db.get_setting(conn, "vr_backfilled") == "1":
            return
        for row in db.auto_vr_rows(conn):
            vr = indexer.detect_vr(row["filename"])
            if vr:
                db.set_vr(conn, row["id"], True, vr["projection"], vr["layout"])
        db.set_setting(conn, "vr_backfilled", "1")


app = FastAPI(title="LocalChannels", lifespan=_lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"

db.init_db()


def _asset_version() -> str:
    """Short hash of the frontend assets, used to bust browser caches whenever
    app.js/style.css change (e.g. after a Docker rebuild that preserves mtimes)."""
    h = hashlib.sha1()
    for name in ("index.html", "app.js", "style.css"):
        try:
            h.update((STATIC_DIR / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


ASSET_VERSION = _asset_version()


# ---------------------------------------------------------------------------
# Simple shared-password protection (optional, off by default)
#
# Enable/disable, the password and how long a sign-in lasts are all set in
# Settings → General and stored in the database; see auth.py.
# ---------------------------------------------------------------------------
auth.init()

PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/api/health"}


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    if not auth.enabled():
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or auth.valid_session(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return RedirectResponse("/login")


@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.post("/api/login")
def api_login(payload: dict = Body(...)):
    if not auth.enabled():
        return {"ok": True}
    if auth.check_password(str(payload.get("password", ""))):
        resp = JSONResponse({"ok": True})
        auth.set_cookie(resp)
        return resp
    return JSONResponse({"detail": "Incorrect password"}, status_code=401)


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    auth.clear_cookie(resp)
    return resp


@app.get("/api/auth")
def api_auth_status():
    return auth.status()


@app.post("/api/auth")
def api_auth_update(payload: dict = Body(...)):
    """Change any of: enabled, password, session_days."""
    enabled = payload.get("enabled")
    password = payload.get("password")
    days = payload.get("session_days")
    if enabled is not None and not isinstance(enabled, bool):
        raise HTTPException(400, "enabled must be true or false")
    if password is not None and not isinstance(password, str):
        raise HTTPException(400, "password must be text")
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise HTTPException(400, "session_days must be a number")
    try:
        status = auth.update(enabled=enabled, password=password, session_days=days)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    resp = JSONResponse({"ok": True, **status})
    # Keep the browser that made the change signed in: turning protection on
    # would otherwise bounce it to the login page, and a new password
    # invalidates every existing session, this one included.
    if status["enabled"]:
        auth.set_cookie(resp)
    else:
        auth.clear_cookie(resp)
    return resp


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__SITE__ — Sign in</title>
<style>
  :root{--bg:#1e1e2e;--surface:#313244;--border:#45475a;--text:#cdd6f4;
        --dim:#a6adc8;--accent:#cba6f7;--ink:#11111b;--danger:#f38ba8;}
  *{box-sizing:border-box;}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
       color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
  .card{width:min(92vw,360px);background:var(--surface);border:1px solid var(--border);
        border-radius:14px;padding:32px;box-shadow:0 12px 40px rgba(0,0,0,.35);}
  .logo{width:44px;height:44px;border-radius:11px;display:grid;place-items:center;margin-bottom:18px;
        background:linear-gradient(135deg,var(--accent),#89b4fa);color:var(--ink);}
  h1{font-size:20px;margin:0 0 4px;letter-spacing:-.02em;}
  p{margin:0 0 22px;color:var(--dim);font-size:14px;}
  label{display:block;font-size:13px;color:var(--dim);margin-bottom:8px;}
  input{width:100%;height:42px;padding:0 14px;background:var(--bg);border:1px solid var(--border);
        border-radius:9px;color:var(--text);outline:none;font-size:15px;}
  input:focus{border-color:var(--accent);}
  button{width:100%;height:42px;margin-top:16px;border:none;border-radius:9px;cursor:pointer;
         background:var(--accent);color:var(--ink);font-weight:600;font-size:15px;}
  button:hover{filter:brightness(1.08);}
  .err{color:var(--danger);font-size:13px;margin-top:12px;min-height:18px;}
</style>
</head>
<body>
  <form class="card" id="f">
    <div class="logo">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
    </div>
    <h1>__SITE__</h1>
    <p>Enter the password to continue.</p>
    <label for="p">Password</label>
    <input id="p" type="password" autocomplete="current-password" autofocus>
    <button type="submit">Sign in</button>
    <div class="err" id="e"></div>
  </form>
  <script>
    const f=document.getElementById('f'),p=document.getElementById('p'),e=document.getElementById('e');
    f.addEventListener('submit',async(ev)=>{
      ev.preventDefault();e.textContent='';
      const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({password:p.value})});
      if(r.ok){location.href='/';}
      else{e.textContent='Incorrect password';p.select();}
    });
  </script>
</body>
</html>"""


@app.get("/login")
def login_page():
    if not auth.enabled():
        return RedirectResponse("/")
    return HTMLResponse(
        LOGIN_HTML.replace("__SITE__", config.site_name),
        headers={"Cache-Control": "no-cache"},
    )

# Kick off a background index on startup if the library looks empty.
def _maybe_initial_index():
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
    if count == 0 and config.root_path.exists():
        indexer.start_reindex()


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

# Cached so serializing a page of videos doesn't hit the settings table once per
# row. Invalidated whenever a relevant setting is saved (see api_set_setting).
_TITLE_CFG: dict = {"value": None}


def _title_config(conn) -> tuple[bool, str]:
    if _TITLE_CFG["value"] is None:
        _TITLE_CFG["value"] = (
            db.get_setting(conn, "filename_tags_strip_title", "1") == "1",
            str(db.get_setting(conn, "filename_tag_brackets", "square") or "square"),
        )
    return _TITLE_CFG["value"]


def display_title(conn, filename: str) -> str:
    """Filename without its extension, and without the bracketed tag section
    when title stripping is on. The file on disk is never renamed."""
    stem = os.path.splitext(filename)[0]
    strip, kind = _title_config(conn)
    return indexer.strip_filename_tags(stem, kind) if strip else stem


def serialize_video(
    conn,
    row,
    include_detail: bool = False,
    stats: Optional[dict[int, tuple[int, Optional[float]]]] = None,
) -> dict:
    vid = row["id"]
    if stats is not None:
        p_count, l_played = stats.get(vid, (0, None))
    else:
        l_played = db.last_played(conn, vid)
        p_count = db.play_count(conn, vid)
    d = {
        "id": vid,
        "rel_path": row["rel_path"],
        "folder": row["folder"],
        "filename": row["filename"],
        "title": display_title(conn, row["filename"]),
        "size": row["size"],
        "duration": row["duration"],
        "width": row["width"],
        "height": row["height"],
        "fps": row["fps"],
        "vcodec": row["vcodec"],
        "acodec": row["acodec"],
        "bitrate": row["bitrate"],
        "container": row["container"],
        "has_thumb": bool(row["thumbnail"]),
        "favorite": bool(row["favorite"]),
        "position": row["position"] or 0,
        "watched": bool(row["watched"]),
        "added_at": row["added_at"],
        "mtime": row["mtime"],
        "vr": bool(row["vr"]),
        "vr_projection": row["vr_projection"] or "eq180",
        "vr_layout": row["vr_layout"] or "mono",
        "last_played": l_played,
        "play_count": p_count,
    }
    if include_detail:
        d["tags"] = db.tags_for_video(conn, vid)
        d["indexed_at"] = row["indexed_at"]
        d["ai_summary"] = row["ai_summary"]
        d["ai_summary_at"] = row["ai_summary_at"]
        d["ai_model"] = row["ai_model"]
    return d


def serialize_videos(conn, rows) -> list[dict]:
    """Serialize a list of video rows using a single bulk play-stats query."""
    if not rows:
        return []
    stats = db.play_stats_map(conn, [r["id"] for r in rows])
    return [serialize_video(conn, r, stats=stats) for r in rows]


# ---------------------------------------------------------------------------
# Config + indexing
# ---------------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    return {**config.public(), "auth_enabled": auth.enabled()}


# Server-side settings the UI can change, with their defaults.
SETTING_DEFAULTS = {
    "filename_tag_brackets": "square",   # square | round | curly
    "filename_tags_auto": "0",           # "1" to auto-tag on every scan
    "filename_tags_strip_title": "1",    # "1" to hide the tag section in titles
    "backup_include_thumbs": "0",        # "1" to put cached thumbnails in backups
    "channel_order": "random",           # random | sequential (by filename)
    "warn_unwritten_tags": "1",          # "1" to flag tags not yet in filenames
    "folder_backdrops": "1",             # "1" to use backdrop.jpg/png as the page background
    "folder_backdrop_dim": "60",         # 0–90: how far the backdrop fades into the theme, in %
    "folder_backdrop_blur": "6",         # 0–40: blur radius in px
    **ai.AI_SETTING_DEFAULTS,            # ai_endpoint, ai_model, ai_frame_mode, ...
}

# Settings stored as "0"/"1"; anything truthy-looking is normalised on save.
_BOOL_SETTINGS = {"filename_tags_auto", "filename_tags_strip_title",
                  "backup_include_thumbs", "warn_unwritten_tags",
                  "ai_disable_thinking", "folder_backdrops"}

# Saving any of these changes how titles are derived, so drop the cache.
_TITLE_SETTINGS = {"filename_tags_strip_title", "filename_tag_brackets"}

# Numeric AI settings and their (minimum, maximum) sanity bounds.
_AI_NUMERIC_BOUNDS = {
    "ai_frame_count": (1, 64),
    "ai_frame_interval": (1, 86400),
    "ai_max_frames": (1, 64),
    "ai_frame_width": (64, 4096),
}

# Every numeric setting, AI or not, validated the same way on save.
_NUMERIC_BOUNDS = {
    **_AI_NUMERIC_BOUNDS,
    "folder_backdrop_dim": (0, 90),
    "folder_backdrop_blur": (0, 40),
}


@app.get("/api/settings")
def api_get_settings():
    with db.get_db() as conn:
        stored = db.all_settings(conn)
    # The password hash and session secret share the table; they have their
    # own endpoint (/api/auth) and must never be handed to the browser.
    stored = {k: v for k, v in stored.items() if not k.startswith("auth_")}
    out = {**SETTING_DEFAULTS, **stored}
    out["ai_thinking"] = ai.thinking_mode(stored)
    return out


@app.post("/api/settings")
def api_set_setting(payload: dict = Body(...)):
    key = payload.get("key")
    value = payload.get("value")
    if key not in SETTING_DEFAULTS:
        raise HTTPException(400, "Unknown setting")
    if key == "filename_tag_brackets" and value not in indexer.FILENAME_TAG_BRACKETS:
        raise HTTPException(400, "Invalid bracket type")
    if key == "channel_order" and value not in ("random", "sequential"):
        raise HTTPException(400, "Channel order must be 'random' or 'sequential'")
    if key in _BOOL_SETTINGS:
        value = "1" if str(value).strip().lower() in ("1", "true", "yes", "on") else "0"
    if key == "ai_thinking":
        v = str(value or "").strip().lower()
        if v in ("enabled", "on", "true", "1"):
            value = "enabled"
        elif v in ("disabled", "off", "false", "0"):
            value = "disabled"
        elif v == "default":
            value = "default"
        else:
            raise HTTPException(
                400, "Thinking mode must be 'default', 'enabled' or 'disabled'"
            )
    if key == "ai_frame_mode" and value not in ("count", "interval"):
        raise HTTPException(400, "Frame mode must be 'count' or 'interval'")
    if key in _NUMERIC_BOUNDS:
        lo, hi = _NUMERIC_BOUNDS[key]
        try:
            n = float(str(value))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} must be a number")
        if not (lo <= n <= hi):
            raise HTTPException(400, f"{key} must be between {lo} and {hi}")
        value = str(int(n))
    with db.get_db() as conn:
        db.set_setting(conn, key, str(value))
        if key == "ai_thinking":
            db.set_setting(conn, "ai_disable_thinking", "1" if value == "disabled" else "0")
        elif key == "ai_disable_thinking":
            db.set_setting(conn, "ai_thinking", "disabled" if value == "1" else "default")
    if key in _TITLE_SETTINGS:
        _TITLE_CFG["value"] = None
    return {"ok": True, "key": key, "value": str(value)}


@app.post("/api/reindex")
def api_reindex(force: bool = Query(False)):
    if not indexer.start_reindex(force=force):
        return {"status": "already_running", **indexer.STATUS}
    return {"status": "started"}


@app.get("/api/index/status")
def api_index_status():
    return indexer.STATUS


@app.get("/api/thumbnails/orphans")
def api_thumbnail_orphans():
    """How many cached posters no longer belong to any video."""
    with db.get_db() as conn:
        return indexer.orphan_summary(conn)


@app.post("/api/thumbnails/cleanup")
def api_thumbnail_cleanup():
    # A running scan is still writing posters for rows it hasn't inserted
    # yet; sweeping under it could delete a brand-new thumbnail. The scan
    # sweeps for itself when it finishes anyway.
    if indexer.STATUS["running"]:
        raise HTTPException(409, "A library scan is running — it will clean up "
                                 "thumbnails when it finishes")
    with db.get_db() as conn:
        return {"ok": True, **indexer.cleanup_thumbnails(conn)}


# ---------------------------------------------------------------------------
# Backup (single file, overwritten each run)
# ---------------------------------------------------------------------------

@app.get("/api/backup")
def api_backup_status():
    return backup.status()


@app.post("/api/backup")
def api_backup_create(payload: dict = Body(default={})):
    inc = (payload or {}).get("include_thumbnails")
    try:
        return backup.create_backup(None if inc is None else bool(inc))
    except Exception as exc:  # disk full, permissions, corrupt DB, ...
        raise HTTPException(500, f"Backup failed: {exc}")


@app.get("/api/backup/download")
def api_backup_download():
    info = backup.status()
    if not info["exists"]:
        raise HTTPException(404, "No backup has been created yet")
    return FileResponse(
        backup.BACKUP_PATH,
        media_type="application/zip",
        filename=backup.download_name(info["created_at"]),
    )


# ---------------------------------------------------------------------------
# AI video analysis (OpenAI-compatible VLM endpoint, e.g. vLLM)
# ---------------------------------------------------------------------------

@app.get("/api/ai/status")
def api_ai_status():
    return ai.STATUS


@app.post("/api/ai/cancel")
def api_ai_cancel():
    ai.cancel_job()
    return {"ok": True}


@app.post("/api/ai/test")
def api_ai_test():
    """Text-only round trip to check endpoint / model / API key."""
    with db.get_db() as conn:
        settings = ai.get_ai_settings(conn)
    try:
        reply = ai.test_connection(settings)
    except ai.AIError as e:
        # 200, deliberately. This used to answer 502, which put a misleading
        # "Bad Gateway" in the access log and — because the client reads the
        # status text on a failed response — showed the user that instead of
        # the actual reason the check failed.
        return {"ok": False, "error": str(e),
                "model": settings.get("ai_model") or "default"}
    return {"ok": True, "reply": reply[:200],
            "model": settings.get("ai_model") or "default"}


@app.post("/api/ai/analyze")
def api_ai_analyze(payload: dict = Body(default={})):
    """Start a background analysis job.

    Payload options (pick one scope):
      {"video_id": 42}          — a single video (always re-analysed)
      {"folder": "path/rel"}    — every video in that folder + subfolders
      {"all": true}             — the entire library
    For folder/all, videos that already have a summary are skipped unless
    "force": true is given.
    """
    if ai.STATUS["running"]:
        return {"status": "already_running", **ai.STATUS}

    force = bool(payload.get("force"))
    video_id = payload.get("video_id")
    folder = (payload.get("folder") or "").strip("/")
    everything = bool(payload.get("all"))

    with db.get_db() as conn:
        if video_id is not None:
            if not db.get_video(conn, int(video_id)):
                raise HTTPException(404, "Video not found")
            ids = [int(video_id)]
        elif everything or folder or payload.get("folder") == "":
            skip = "" if force else "AND (ai_summary IS NULL OR ai_summary = '')"
            if everything or not folder:
                rows = conn.execute(
                    f"SELECT id FROM videos WHERE 1=1 {skip} ORDER BY rel_path"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT id FROM videos WHERE (folder = ? OR folder LIKE ? ESCAPE '\\') "
                    f"{skip} ORDER BY rel_path",
                    (folder, f"{db.escape_like(folder)}/%"),
                ).fetchall()
            ids = [r["id"] for r in rows]
        else:
            raise HTTPException(400, "Give video_id, folder, or all=true")

        # Fail fast with a clear message if the endpoint isn't configured.
        settings = ai.get_ai_settings(conn)
    try:
        ai.chat_url(settings.get("ai_endpoint", ""))
    except ai.AIError as e:
        raise HTTPException(400, str(e))

    if not ids:
        return {"status": "nothing_to_do", "queued": 0}
    if not ai.start_job(ids):
        return {"status": "already_running", **ai.STATUS}
    return {"status": "started", "queued": len(ids)}


@app.delete("/api/video/{video_id}/ai-summary")
def api_clear_ai_summary(video_id: int):
    with db.get_db() as conn:
        if not db.get_video(conn, video_id):
            raise HTTPException(404, "Video not found")
        db.set_ai_summary(conn, video_id, None, None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Maintenance: duplicate detection + file deletion
# ---------------------------------------------------------------------------

def _file_hash(row) -> Optional[str]:
    """SHA-256 of the file's contents, cached in the DB and recomputed only when
    the file's mtime changes. Returns None if the file is missing/unreadable.

    Disk reads happen outside DB transactions so hashing large files never holds
    a SQLite write lock.
    """
    if row["content_hash"] and row["hash_mtime"] == row["mtime"]:
        return row["content_hash"]
    path = config.root_path / row["rel_path"]
    if not path.exists():
        return None
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    digest = h.hexdigest()
    with db.get_db() as conn:
        db.set_content_hash(conn, row["id"], digest, row["mtime"])
    return digest


# ---------------------------------------------------------------------------
# About / updates
# ---------------------------------------------------------------------------

@app.get("/api/about")
def api_about():
    return about.load()


@app.get("/api/about/updates")
def api_check_updates(force: bool = Query(False)):
    """Check GitHub for a newer release. Only ever runs when asked."""
    return about.check_for_updates(force=force)


@app.get("/api/duplicates")
def api_duplicates(method: str = Query("hash"),
                   tolerance_ms: int = Query(0, ge=0, le=60000)):
    """Find likely-duplicate videos.

    method=hash     — byte-identical files (exact SHA-256 match; size+quick_hash prefiltered)
    method=size     — same exact file size
    method=duration — same runtime, compared at millisecond resolution.
                      `tolerance_ms` widens the match window (0 = exact to the
                      millisecond, which is what you want for a large library —
                      second-level rounding groups thousands of unrelated clips).

    Groups the user has dismissed come back marked `ignored` rather than
    omitted, so the UI can offer to show them again without a second request.
    A dismissed group that has since gained a member no longer matches its
    record and so comes back unignored, for review.
    """
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM videos").fetchall()
        ignores = db.ignored_dupe_sets(conn)

    raw_groups: list[list] = []

    def collect(items):
        if len(items) >= 2:
            raw_groups.append(items)

    if method == "size":
        buckets: dict = {}
        for r in rows:
            if r["size"]:
                buckets.setdefault(r["size"], []).append(r)
        for items in buckets.values():
            collect(items)
    elif method == "duration":
        # Walk in duration order and break a group whenever the gap to the
        # previous video exceeds the tolerance. Avoids the bucket-boundary
        # problem where two near-identical runtimes round into different
        # buckets (and vice versa).
        timed = sorted(
            (r for r in rows if r["duration"]), key=lambda r: r["duration"]
        )
        run: list = []
        prev_ms = 0
        for r in timed:
            ms = round(r["duration"] * 1000)
            if run and (ms - prev_ms) > tolerance_ms:
                collect(run)
                run = []
            run.append(r)
            prev_ms = ms
        collect(run)
    else:  # hash — prefilter by size and quick_hash, then confirm with content hash
        by_size: dict = {}
        for r in rows:
            if r["size"]:
                by_size.setdefault(r["size"], []).append(r)
        for items in by_size.values():
            if len(items) < 2:
                continue
            by_qh: dict = {}
            for it in items:
                qh = it["quick_hash"]
                if not qh:
                    qh = indexer.quick_hash(config.root_path / it["rel_path"], it["size"])
                    if qh:
                        with db.get_db() as conn:
                            db.set_quick_hash(conn, it["id"], qh)
                if qh:
                    by_qh.setdefault(qh, []).append(it)
            for qh_items in by_qh.values():
                if len(qh_items) < 2:
                    continue
                by_hash: dict = {}
                for it in qh_items:
                    digest = _file_hash(it)
                    if digest:
                        by_hash.setdefault(digest, []).append(it)
                for dups in by_hash.values():
                    collect(dups)

    def match_ignore(ids: set[int]) -> Optional[int]:
        """The ignore covering this group, if there is one."""
        for gid, members in ignores.items():
            if members and ids <= members:
                return gid
        return None

    groups: list[dict] = []
    if raw_groups:
        all_ids = [it["id"] for grp in raw_groups for it in grp]
        with db.get_db() as conn:
            stats = db.play_stats_map(conn, all_ids)
            for items in raw_groups:
                durations = [it["duration"] for it in items if it["duration"] is not None]
                ids = {it["id"] for it in items}
                gid = match_ignore(ids)
                groups.append({
                    "videos": [serialize_video(conn, it, stats=stats) for it in items],
                    "size": items[0]["size"],
                    "duration": items[0]["duration"],
                    "duration_min": min(durations) if durations else None,
                    "duration_max": max(durations) if durations else None,
                    "video_ids": sorted(ids),
                    "ignored": gid is not None,
                    "ignore_id": gid,
                })

    groups.sort(key=lambda g: (g["size"] or 0) * len(g["videos"]), reverse=True)
    return {
        "method": method,
        "tolerance_ms": tolerance_ms,
        "groups": groups,
        "ignored": sum(1 for g in groups if g["ignored"]),
    }


@app.post("/api/duplicates/ignore")
def api_duplicates_ignore(payload: dict = Body(...)):
    """Dismiss a duplicate group so it stops appearing in scans."""
    ids = sorted({int(v) for v in (payload.get("video_ids") or [])})
    if len(ids) < 2:
        raise HTTPException(400, "A group needs at least two videos")
    method = payload.get("method") or None
    with db.get_db() as conn:
        present = {r["id"] for r in conn.execute(
            f"SELECT id FROM videos WHERE id IN ({', '.join('?' for _ in ids)})", ids
        ).fetchall()}
        missing = [i for i in ids if i not in present]
        if missing:
            raise HTTPException(404, "Some of those videos are no longer indexed")
        # A group that grew re-appears for review; dismissing it again should
        # replace the earlier, smaller record rather than stack on top of it.
        for gid, members in db.ignored_dupe_sets(conn).items():
            if members <= set(ids):
                db.lift_dupe_ignore(conn, gid)
        gid = db.ignore_dupe_group(conn, ids, method)
    return {"ok": True, "ignore_id": gid, "videos": len(ids)}


@app.delete("/api/duplicates/ignore/{ignore_id}")
def api_duplicates_unignore(ignore_id: int):
    with db.get_db() as conn:
        db.lift_dupe_ignore(conn, ignore_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Media reports: CSV export and browser-compatibility checks
# ---------------------------------------------------------------------------

def _scope_label(folder: str, recursive: bool) -> str:
    f = (folder or "").strip("/")
    if not f:
        return "whole library" if recursive else "library root only"
    return f"{f}{' and subfolders' if recursive else ' only'}"


@app.get("/api/report.csv")
def api_report_csv(
    folder: str = Query("", description="folder relative to the library root; empty = everything"),
    recursive: bool = Query(True, description="include subfolders"),
    detailed: bool = Query(False, description="re-probe every file with ffprobe for "
                                              "tracks, languages, embedded tags and chapters"),
    only_issues: bool = Query(False, description="only files browsers can't (fully) play"),
):
    """Per-file media report as CSV, streamed so big detailed reports don't time out."""
    with db.get_db() as conn:
        rows = report.scoped_videos(conn, folder, recursive)
        maps = report._bulk_maps(conn)
    if only_issues:
        rows = [r for r in rows
                if compat.classify(r["rel_path"], r["container"], r["vcodec"],
                                   r["acodec"])["issues"]]
    name = report.filename_for(folder, recursive, detailed, only_issues)
    return StreamingResponse(
        report.csv_stream(rows, maps, detailed),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "X-Report-Rows": str(len(rows))},
    )


@app.get("/api/incompatible")
def api_incompatible(folder: str = Query(""), recursive: bool = Query(True)):
    """Files browsers can't play, or play only in some setups, with fix commands."""
    with db.get_db() as conn:
        out = report.incompatible(conn, folder, recursive)
    out["scope"] = _scope_label(folder, recursive)
    return out


@app.get("/api/incompatible/fix.sh")
def api_incompatible_script(
    folder: str = Query(""),
    recursive: bool = Query(True),
    kinds: str = Query("", description="issue kinds to include, comma-separated; empty = all"),
):
    """Bash script that converts or renames every listed file."""
    with db.get_db() as conn:
        items = report.incompatible(conn, folder, recursive)["items"]
    wanted = set(_csv(kinds))
    if wanted:
        items = [it for it in items if wanted & {i["kind"] for i in it["issues"]}]
    label = _scope_label(folder, recursive)
    if wanted:
        label += " (" + ", ".join(compat.ISSUE_LABELS.get(k, k) for k in sorted(wanted)) + ")"
    scope = (folder or "").strip("/").replace("/", "_") or "library"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in scope)
    return Response(
        report.fix_script(items, label),
        media_type="text/x-shellscript; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="localchannels-fix-{safe}.sh"'},
    )


@app.delete("/api/video/{video_id}/file")
def api_delete_file(video_id: int):
    """Delete a video's file from disk and remove it from the library."""
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row:
            raise HTTPException(404, "Video not found")
        path = (config.root_path / row["rel_path"]).resolve()
        try:
            path.relative_to(config.root_path.resolve())  # guard against traversal
        except ValueError:
            raise HTTPException(400, "Refusing to delete outside the library root")
        deleted = False
        if path.exists():
            try:
                path.unlink()
                deleted = True
            except OSError as e:
                raise HTTPException(500, f"Could not delete file: {e}")
        indexer.remove_thumbnail(db.delete_video(conn, video_id))
    return {"ok": True, "deleted_file": deleted}


@app.post("/api/video/{video_id}/remove")
def api_remove_from_library(video_id: int):
    """Remove a video from the library but leave the file on disk. The path is
    remembered so a later rescan won't re-add it."""
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row:
            raise HTTPException(404, "Video not found")
        db.ignore_file(conn, row["rel_path"])
        indexer.remove_thumbnail(db.delete_video(conn, video_id))
    return {"ok": True, "kept_file": True}


@app.post("/api/videos/delete")
def api_bulk_delete(payload: dict = Body(...)):
    """Delete multiple videos' files from disk and remove them from the library."""
    ids = [int(x) for x in (payload.get("video_ids") or [])]
    if not ids:
        raise HTTPException(400, "No videos given")
    root = config.root_path.resolve()
    removed = deleted_files = 0
    with db.get_db() as conn:
        for vid in ids:
            row = db.get_video(conn, vid)
            if not row:
                continue
            path = (config.root_path / row["rel_path"]).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.exists():
                try:
                    path.unlink()
                    deleted_files += 1
                except OSError:
                    pass
            indexer.remove_thumbnail(db.delete_video(conn, vid))
            removed += 1
    return {"ok": True, "removed": removed, "deleted_files": deleted_files}


# ---------------------------------------------------------------------------
# Browsing
# ---------------------------------------------------------------------------

SORT_COLUMNS = {
    "name": "filename COLLATE NOCASE",
    "added": "added_at",
    "file": "mtime",
    "length": "duration",
    # Pixel count, so 1920x800 sorts above 1280x720. NULLs sort as 0 either way.
    "resolution": "COALESCE(width, 0) * COALESCE(height, 0)",
    "size": "COALESCE(size, 0)",
}


def _sort_column(sort: str) -> str:
    """SQL expression to order by, for one of the SORT_COLUMNS keys.

    Sorting by name follows the *displayed* title, so a file called
    "[INTRO] Opening.mp4" that shows as "Opening" also sorts under O rather than
    under the bracket. Only a leading tag section can change the ordering, so
    that is all this strips; the bracket characters come from a fixed table and
    are never user-supplied text.
    """
    if sort != "name":
        return SORT_COLUMNS.get(sort, SORT_COLUMNS["name"])
    with db.get_db() as conn:
        strip, kind = _title_config(conn)
    if not strip:
        return SORT_COLUMNS["name"]
    open_c, close_c = indexer.FILENAME_TAG_BRACKETS.get(
        kind, indexer.FILENAME_TAG_BRACKETS["square"]
    )
    o, c = open_c.replace("'", "''"), close_c.replace("'", "''")
    return (
        f"CASE WHEN substr(filename, 1, 1) = '{o}' AND instr(filename, '{c}') > 0 "
        f"THEN ltrim(substr(filename, instr(filename, '{c}') + 1), ' -_') "
        f"ELSE filename END COLLATE NOCASE"
    )


@app.get("/api/browse")
def api_browse(
    path: str = Query("", description="folder relative to root"),
    sort: str = Query("name"),
    order: str = Query("asc"),
):
    """Return immediate subfolders and videos within a folder."""
    path = path.strip("/")
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with db.get_db() as conn:
        # Subfolders and recursive counts in a single GROUP BY query.
        all_folders = db.folder_tree_counts(conn)
        prefix = f"{path}/" if path else ""
        subfolders: dict[str, int] = {}
        for folder, cnt in all_folders.items():
            if path:
                if not folder.startswith(prefix):
                    continue
                remainder = folder[len(prefix):]
            else:
                remainder = folder
            if not remainder or "/" in remainder:
                continue
            subfolders[folder] = cnt

        # Videos directly in this folder, sorted per the global preference
        # (NULLs always last so missing durations/dates don't jump to the top).
        vrows = conn.execute(
            f"SELECT * FROM videos WHERE folder = ? "
            f"ORDER BY ({col} IS NULL), {col} {direction}",
            (path,),
        ).fetchall()
        videos = serialize_videos(conn, vrows)

    folders = [
        {"path": p, "name": p.split("/")[-1], "count": c,
         "image": folderart.url(p, "folder")}
        for p, c in sorted(subfolders.items())
    ]
    return {"path": path, "folders": folders, "videos": videos,
            "backdrop": folderart.url(path, "backdrop")}


@app.get("/api/videos")
def api_all_videos(
    sort: str = Query("name"),
    order: str = Query("asc"),
    limit: int = Query(0, ge=0, le=500),
    offset: int = Query(0, ge=0),
):
    """Every video in the library, sorted and optionally paginated.
    `count` is always the full library total (for building page controls)."""
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with db.get_db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
        q = f"SELECT * FROM videos ORDER BY ({col} IS NULL), {col} {direction}"
        if limit:
            rows = conn.execute(q + " LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        else:
            rows = conn.execute(q).fetchall()
        videos = serialize_videos(conn, rows)
    return {"videos": videos, "count": total, "limit": limit, "offset": offset}


@app.get("/api/vr")
def api_vr_videos(sort: str = Query("name"), order: str = Query("asc")):
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with db.get_db() as conn:
        rows = db.vr_videos(conn, f"({col} IS NULL), {col} {direction}")
        videos = serialize_videos(conn, rows)
    return {"videos": videos, "count": len(videos)}


@app.get("/api/stats")
def api_stats():
    with db.get_db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
        vr = db.count_vr(conn)
    return {"total": total, "vr": vr}


@app.get("/api/stats/library")
def api_library_stats():
    """Everything behind Settings → Statistics."""
    with db.get_db() as conn:
        stats = db.library_stats(conn)
        # Attach display titles wherever a specific video is referenced, so
        # the page shows the same clean names as the rest of the app.
        for v in stats["popular"]:
            v["title"] = display_title(conn, v.pop("filename"))
        for rec in stats["records"].values():
            if rec:
                rec["title"] = display_title(conn, rec.pop("filename"))
    return stats


@app.post("/api/video/{video_id}/vr")
def api_set_vr(video_id: int, payload: dict = Body(...)):
    vr = bool(payload.get("vr"))
    projection = payload.get("projection") or "eq180"
    layout = payload.get("layout") or "mono"
    if projection not in ("eq360", "eq180", "fisheye"):
        raise HTTPException(400, "Bad projection")
    if layout not in ("mono", "sbs", "tb"):
        raise HTTPException(400, "Bad layout")
    with db.get_db() as conn:
        if not db.get_video(conn, video_id):
            raise HTTPException(404, "Video not found")
        db.set_vr(conn, video_id, vr, projection if vr else None, layout if vr else None, manual=True)
    return {"ok": True, "vr": vr, "projection": projection, "layout": layout}


@app.get("/api/tree")
def api_tree():
    """Full folder tree for the sidebar."""
    with db.get_db() as conn:
        counts = db.folder_tree_counts(conn)
    result = [
        {"path": f, "name": f.split("/")[-1], "depth": f.count("/"), "count": counts[f]}
        for f in sorted(counts)
    ]
    return {"folders": result}


# ---------------------------------------------------------------------------
# Video detail, playback recording, tags
# ---------------------------------------------------------------------------

@app.get("/api/video/{video_id}")
def api_video(video_id: int):
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row:
            raise HTTPException(404, "Video not found")
        data = serialize_video(conn, row, include_detail=True)
        hist = conn.execute(
            "SELECT played_at FROM watch_history WHERE video_id = ? "
            "ORDER BY played_at DESC LIMIT 20",
            (video_id,),
        ).fetchall()
        data["history"] = [h["played_at"] for h in hist]
    return data


@app.post("/api/video/{video_id}/played")
def api_played(video_id: int):
    with db.get_db() as conn:
        if not db.get_video(conn, video_id):
            raise HTTPException(404, "Video not found")
        db.record_play(conn, video_id)
        played = db.last_played(conn, video_id)
    return {"ok": True, "last_played": played}


@app.get("/api/history")
def api_history(limit: int = Query(100, le=500)):
    with db.get_db() as conn:
        rows = db.recent_history(conn, limit)
        stats = db.play_stats_map(conn, [r["id"] for r in rows])
        out = []
        for r in rows:
            v = serialize_video(conn, r, stats=stats)
            v["played_at"] = r["played_at"]
            out.append(v)
    return {"videos": out}


@app.post("/api/video/{video_id}/favorite")
def api_set_favorite(video_id: int, payload: dict = Body(...)):
    value = bool(payload.get("value", True))
    with db.get_db() as conn:
        if not db.get_video(conn, video_id):
            raise HTTPException(404, "Video not found")
        db.set_favorite(conn, video_id, value)
    return {"favorite": value}


@app.get("/api/favorites")
def api_favorites():
    with db.get_db() as conn:
        rows = db.favorites(conn)
        videos = serialize_videos(conn, rows)
    return {"videos": videos}


# Progress thresholds: below this fraction a video isn't considered watched or
# resumeable; at/above the upper one it's marked watched and the resume point is
# cleared so it plays from the start next time.
WATCHED_MIN_FRACTION = 0.05
WATCHED_DONE_FRACTION = 0.90


@app.post("/api/video/{video_id}/progress")
def api_progress(video_id: int, payload: dict = Body(...)):
    position = float(payload.get("position") or 0)
    duration = float(payload.get("duration") or 0)
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row:
            raise HTTPException(404, "Video not found")
        dur = duration or (row["duration"] or 0)
        frac = (position / dur) if dur > 0 else 0.0
        watched = bool(row["watched"])
        if frac >= WATCHED_DONE_FRACTION:
            watched = True
            new_position = 0.0          # finished → start over next time
        elif frac <= WATCHED_MIN_FRACTION:
            new_position = 0.0          # too little watched → not resumeable
        else:
            new_position = position     # keep a resume point
        db.set_watch_state(conn, video_id, new_position, watched)
    return {"position": new_position, "watched": watched, "fraction": round(frac, 4)}


@app.get("/api/new")
def api_new(limit: int = Query(100, le=500)):
    with db.get_db() as conn:
        rows = db.newest(conn, limit)
        videos = serialize_videos(conn, rows)
    return {"videos": videos}


@app.post("/api/watched/clear")
def api_clear_watched():
    """Remove the Watched flag from every video. Play history and resume
    points are untouched."""
    with db.get_db() as conn:
        cur = conn.execute("UPDATE videos SET watched = 0 WHERE watched = 1")
        cleared = cur.rowcount
    return {"ok": True, "cleared": cleared}


@app.post("/api/history/clear")
def api_clear_history():
    """Delete all play history. Empties the Watched page and resets every
    video's last-played time and play count."""
    with db.get_db() as conn:
        cur = conn.execute("DELETE FROM watch_history")
        cleared = cur.rowcount
    return {"ok": True, "cleared": cleared}


@app.get("/api/tags")
def api_tags():
    with db.get_db() as conn:
        return {"tags": db.all_tags(conn), "untagged": db.untagged_count(conn)}


@app.post("/api/tags")
def api_create_tag(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Tag name required")
    with db.get_db() as conn:
        db.create_tag(conn, name)
        return {"tags": db.all_tags(conn)}


@app.patch("/api/tags/{name}")
def api_rename_tag(name: str, payload: dict = Body(...)):
    new = (payload.get("name") or "").strip()
    if not new:
        raise HTTPException(400, "New tag name required")
    with db.get_db() as conn:
        if not db.rename_tag(conn, name, new):
            raise HTTPException(404, "Tag not found")
        return {"tags": db.all_tags(conn)}


@app.delete("/api/tags/{name}")
def api_delete_tag(name: str):
    with db.get_db() as conn:
        db.delete_tag(conn, name)
        return {"tags": db.all_tags(conn)}


@app.post("/api/tags/from-filenames")
def api_tags_from_filenames(payload: dict = Body(default={})):
    """Scan every filename and add tags from its bracketed section."""
    brackets = (payload or {}).get("brackets")
    with db.get_db() as conn:
        if not brackets:
            brackets = db.get_setting(conn, "filename_tag_brackets", "square")
        if brackets not in indexer.FILENAME_TAG_BRACKETS:
            raise HTTPException(400, "Invalid bracket type")
        rows = conn.execute("SELECT id, filename FROM videos").fetchall()
        videos_tagged = 0
        tags_added = 0
        for r in rows:
            tags = indexer.extract_filename_tags(r["filename"], brackets)
            if not tags:
                continue
            before = set(db.tags_for_video(conn, r["id"]))
            for t in tags:
                db.add_tag(conn, r["id"], t)
            added = len(set(db.tags_for_video(conn, r["id"])) - before)
            if added:
                tags_added += added
            videos_tagged += 1
    return {"ok": True, "videos_tagged": videos_tagged, "tags_added": tags_added, "brackets": brackets}


# Characters that must never end up in a filename, plus the bracket characters
# themselves (they would break the round-trip back into tags).
_TAG_FILENAME_BANNED = set('[](){}/\\:*?"<>|')

# Preview payloads are capped so a huge library doesn't return megabytes of JSON.
_PLAN_PREVIEW_LIMIT = 400


def _tag_write_problem(tag: str) -> Optional[str]:
    """Why this tag can't be written into a filename, or None if it's fine."""
    if not tag.strip():
        return "empty tag"
    if any(ch.isspace() for ch in tag):
        return f"“{tag}” contains a space (tags are space-separated in filenames)"
    if any(ch in _TAG_FILENAME_BANNED for ch in tag):
        return f"“{tag}” contains a character that isn't safe in a filename"
    return None


def _plan_tag_writes(conn, check_disk: bool = True) -> list[dict]:
    """Work out the rename each tagged video would need, without modifying disk.

    Videos with no tags are left alone entirely — this writes tags out, it never
    strips a bracket section off a file the app doesn't have tags for.
    When `check_disk=False` (used by the toolbar poll `/api/tags/pending`),
    collisions are checked against the indexed library paths in memory so the
    poll never touches the filesystem.
    """
    kind = str(db.get_setting(conn, "filename_tag_brackets", "square") or "square")
    open_c, close_c = indexer.FILENAME_TAG_BRACKETS.get(
        kind, indexer.FILENAME_TAG_BRACKETS["square"]
    )
    tags_by_vid = db.video_tags_map(conn)
    if not tags_by_vid:
        return []
    rows = conn.execute(
        "SELECT id, rel_path, folder, filename FROM videos ORDER BY rel_path"
    ).fetchall()
    indexed_paths = {r["rel_path"] for r in rows}

    plan: list[dict] = []
    claimed: dict[str, int] = {}       # target rel_path -> video id that wants it
    for r in rows:
        tags = tags_by_vid.get(r["id"])
        if not tags:
            continue

        item = {"id": r["id"], "from": r["rel_path"], "to": None,
                "status": "rename", "reason": None}

        problem = next((p for p in (_tag_write_problem(t) for t in tags) if p), None)
        if problem:
            item.update(status="skipped", reason=problem)
            plan.append(item)
            continue

        stem, ext = os.path.splitext(r["filename"])
        base = indexer.strip_filename_tags(stem, kind)
        new_name = f"{base} {open_c}{' '.join(tags)}{close_c}{ext}"
        new_rel = f"{r['folder']}/{new_name}" if r["folder"] else new_name
        item["to"] = new_rel

        existing_tags = indexer.extract_filename_tags(r["filename"], kind)
        already_has_tags = (
            new_name == r["filename"]
            or (set(existing_tags) == set(tags)
                and f"{base} {open_c}{' '.join(existing_tags)}{close_c}{ext}" == r["filename"])
        )

        if already_has_tags:
            item.update(status="unchanged")
        elif new_rel in claimed:
            item.update(status="skipped",
                        reason="another video would be renamed to this same name")
        elif new_rel in indexed_paths or (check_disk and (config.root_path / new_rel).exists()):
            item.update(status="skipped", reason="a file with that name already exists")
        else:
            claimed[new_rel] = r["id"]
        plan.append(item)
    return plan


def _plan_summary(plan: list[dict]) -> dict:
    counts = {"rename": 0, "unchanged": 0, "skipped": 0}
    for item in plan:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def _safe_target(rel_path: str) -> Path:
    """Resolve a library-relative path, refusing anything outside the root."""
    root = config.root_path.resolve()
    target = (config.root_path / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Refusing to write outside the library root")
    return target


@app.get("/api/tags/pending")
def api_tags_pending():
    """How many videos have tags not yet written into their filenames.

    Cheap counterpart to /api/tags/to-filenames: the UI polls this to decide
    whether to show the unwritten-tags warning, so it returns counts only and
    never touches disk.
    """
    with db.get_db() as conn:
        if db.get_setting(conn, "warn_unwritten_tags", "1") != "1":
            return {"enabled": False, "pending": 0, "skipped": 0}
        summary = _plan_summary(_plan_tag_writes(conn, check_disk=False))
    return {"enabled": True, "pending": summary["rename"], "skipped": summary["skipped"]}


@app.post("/api/tags/to-filenames")
def api_tags_to_filenames(payload: dict = Body(default={})):
    """Write each video's tags back into its filename on disk.

    The inverse of /api/tags/from-filenames. Without `apply` it only returns a
    preview, so the UI can show exactly what would change before anything moves.
    Renames keep the same database row, so tags, favorites, watch history and
    resume positions all survive.
    """
    apply = bool((payload or {}).get("apply"))
    with db.get_db() as conn:
        plan = _plan_tag_writes(conn)
        summary = _plan_summary(plan)
        preview = [i for i in plan if i["status"] != "unchanged"][:_PLAN_PREVIEW_LIMIT]

        if not apply:
            return {"applied": False, "counts": summary, "items": preview,
                    "truncated": summary["rename"] + summary["skipped"] > len(preview)}

        renamed, failed = 0, []
        row_thumbs = {r["id"]: r["thumbnail"] for r in
                      conn.execute("SELECT id, thumbnail FROM videos").fetchall()}
        for item in plan:
            if item["status"] != "rename":
                continue
            src = _safe_target(item["from"])
            dst = _safe_target(item["to"])
            if not src.exists():
                failed.append({**item, "error": "file no longer exists"})
                continue
            if dst.exists():          # re-checked: disk may have changed since preview
                failed.append({**item, "error": "a file with that name already exists"})
                continue
            try:
                src.rename(dst)
            except OSError as exc:
                msg = str(exc)
                if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                    msg = ("the library looks read-only — mount it read/write "
                           "(drop the :ro from the volume in docker-compose.yaml)")
                failed.append({**item, "error": msg})
                continue

            # Thumbnails are named from a hash of the path, so move that too.
            thumb = indexer.move_thumbnail(item["from"], item["to"], row_thumbs.get(item["id"]))
            db.move_video(conn, item["id"], item["to"],
                          os.path.basename(item["to"]), thumb)
            renamed += 1

    return {"applied": True, "renamed": renamed, "counts": summary,
            "failed": failed[:_PLAN_PREVIEW_LIMIT], "failed_count": len(failed)}


@app.post("/api/video/{video_id}/tags")
def api_add_tag(video_id: int, payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Tag name required")
    with db.get_db() as conn:
        if not db.get_video(conn, video_id):
            raise HTTPException(404, "Video not found")
        db.add_tag(conn, video_id, name)
        tags = db.tags_for_video(conn, video_id)
    return {"tags": tags}


@app.delete("/api/video/{video_id}/tags/{name}")
def api_remove_tag(video_id: int, name: str):
    with db.get_db() as conn:
        db.remove_tag(conn, video_id, name)
        tags = db.tags_for_video(conn, video_id)
    return {"tags": tags}


@app.post("/api/videos/tags")
def api_bulk_tag(payload: dict = Body(...)):
    """Apply one or more tags to many videos at once (mass tagging)."""
    video_ids = payload.get("video_ids") or []
    tag_names = payload.get("tags") or []
    video_ids = [int(v) for v in video_ids]
    tag_names = [str(t).strip() for t in tag_names if str(t).strip()]
    if not video_ids or not tag_names:
        raise HTTPException(400, "Need video_ids and tags")
    with db.get_db() as conn:
        db.bulk_add_tags(conn, video_ids, tag_names)
    return {"ok": True, "tagged": len(video_ids), "tags": tag_names}


@app.get("/api/untagged")
def api_untagged(sort: str = Query("name"), order: str = Query("asc")):
    """Videos with no tags — the working list for tagging up a library."""
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with db.get_db() as conn:
        rows = db.untagged_videos(conn, f"{col} {direction}")
        videos = serialize_videos(conn, rows)
    return {"videos": videos, "total": len(videos)}


def _csv(value: str) -> list[str]:
    """Split a comma-separated query parameter into clean names."""
    return [p.strip() for p in (value or "").split(",") if p.strip()]


@app.get("/api/search")
def api_search(
    q: str = Query("", description="substring of the file path"),
    # aliases keep the query string readable without shadowing any()/all()
    any_: str = Query("", alias="any", description="tags; match at least one"),
    all_: str = Query("", alias="all", description="tags; must have every one"),
    exclude: str = Query("", description="tags; must have none of these"),
    folder: str = Query("", description="restrict to this folder and subfolders"),
    untagged: bool = Query(False, description="only videos with no tags"),
    sort: str = Query("name"),
    order: str = Query("asc"),
    limit: int = Query(0, ge=0, le=2000, description="0 returns everything"),
    offset: int = Query(0, ge=0),
    facets: bool = Query(False, description="also return per-tag counts over "
                                            "the whole result set"),
):
    """Filter the library by tags and/or text.

    Replaces the old client-side search, which fetched every folder over HTTP
    and filtered in the browser. The three tag clauses are ANDed, so
    any=beach,surf & exclude=blooper reads as "beach or surf, but not blooper".
    """
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    filters = dict(
        any_tags=_csv(any_),
        all_tags=_csv(all_),
        not_tags=_csv(exclude),
        text=q,
        folder=folder,
        untagged=untagged,
    )
    with db.get_db() as conn:
        rows, total = db.filter_videos(
            conn,
            order_by=f"{col} {direction}",
            limit=limit or None,
            offset=offset,
            **filters,
        )
        videos = serialize_videos(conn, rows)
        out = {"videos": videos, "total": total, "count": len(videos)}
        if facets:
            # Tags that co-occur with the current filter, counted over every
            # match (not just this page), for drill-down in the filter UI.
            out["facets"] = db.filter_tag_counts(conn, **filters)
    return out


@app.get("/api/tag/{name}")
def api_tag_videos(name: str):
    with db.get_db() as conn:
        rows = db.videos_for_tag(conn, name)
        videos = serialize_videos(conn, rows)
    return {"tag": name, "videos": videos}


# ---------------------------------------------------------------------------
# Channel mode: shuffle-without-replacement over a folder (+subfolders)/tags
# ---------------------------------------------------------------------------

def _channel_key(folder: str, tags: list[str],
                 exclude_tags: Optional[list[str]] = None,
                 tag_mode: str = "any") -> str:
    """Stable identity for an unsaved channel.

    Every part of the definition goes in, so changing the rule starts a fresh
    rotation instead of inheriting the "already played" list of a channel that
    no longer exists.
    """
    raw = "|".join([
        folder,
        ",".join(sorted(t.lower() for t in tags)),
        ",".join(sorted(t.lower() for t in (exclude_tags or []))),
        "all" if tag_mode == "all" else "any",
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pool(conn, folders: list[str], tags: list[str],
          exclude_tags: Optional[list[str]] = None,
          tag_mode: str = "any") -> list[int]:
    """Video ids for a channel.

    A video is in the pool when it sits in one of the folders (subfolders
    included) OR carries the required tags, and then only if it carries none of
    the excluded ones. A "" folder entry means the whole library.

    `tag_mode` applies to the include list alone: "any" takes a video with at
    least one of the tags, "all" only takes videos carrying every one. The
    exclusion is always "none of these" — one banned tag is enough to drop a
    video, which is what makes it useful for pruning a broad include list.
    """
    raw = [f.strip("/") for f in (folders or [])]
    whole_library = "" in raw
    real_folders = [f for f in raw if f]
    tags = [t.strip() for t in (tags or []) if t.strip()]
    exclude_tags = [t.strip() for t in (exclude_tags or []) if t.strip()]

    clauses: list = []
    params: list = []
    if whole_library:
        clauses.append("1")
    else:
        if real_folders:
            subs = []
            for f in real_folders:
                subs.append("(folder = ? OR folder LIKE ? ESCAPE '\\')")
                params.extend([f, f"{db.escape_like(f)}/%"])
            clauses.append("(" + " OR ".join(subs) + ")")
        if tags:
            ph = ", ".join("?" for _ in tags)
            if tag_mode == "all":
                clauses.append(
                    "(SELECT COUNT(DISTINCT LOWER(t.name)) FROM video_tags vt "
                    "JOIN tags t ON t.id = vt.tag_id "
                    f"WHERE vt.video_id = videos.id AND LOWER(t.name) IN ({ph})) = ?"
                )
                params.extend([t.lower() for t in tags])
                params.append(len(set(t.lower() for t in tags)))
            else:
                clauses.append(
                    "id IN (SELECT vt.video_id FROM video_tags vt "
                    f"JOIN tags t ON t.id = vt.tag_id WHERE LOWER(t.name) IN ({ph}))"
                )
                params.extend([t.lower() for t in tags])
    if not clauses:
        return []

    sql = "SELECT id FROM videos WHERE (" + " OR ".join(clauses) + ")"
    if exclude_tags:
        ph = ", ".join("?" for _ in exclude_tags)
        sql += (" AND id NOT IN (SELECT vt.video_id FROM video_tags vt "
                f"JOIN tags t ON t.id = vt.tag_id WHERE LOWER(t.name) IN ({ph}))")
        params.extend([t.lower() for t in exclude_tags])
    # ORDER BY rel_path so sequential channels play in a predictable order;
    # random mode ignores it, so this costs nothing there.
    sql += " ORDER BY rel_path"
    return [r["id"] for r in conn.execute(sql, params).fetchall()]


def _channel_rule(ch) -> tuple[list, list, list, str]:
    """Unpack a saved channel row into (folders, tags, exclude_tags, tag_mode).

    Rows created before excludes existed have no such columns, hence the
    defensive lookup rather than plain indexing.
    """
    keys = ch.keys()
    folders = json.loads(ch["folders"] or "[]")
    tags = json.loads(ch["tags"] or "[]")
    exclude = json.loads(ch["exclude_tags"] or "[]") if "exclude_tags" in keys else []
    mode = (ch["tag_mode"] if "tag_mode" in keys else "any") or "any"
    return folders, tags, exclude, mode


def _serve_next(conn, key: str, pool: list[int], exclude: Optional[int]) -> dict:
    """Pick the next video from a pool, looping when exhausted.

    Both orders track what's been played in channel_played, so a channel
    resumes where it left off and every video is seen once per lap. The only
    difference is which of the remaining videos comes next: a random one, or
    the next by filename after the one just played.
    """
    if not pool:
        raise HTTPException(404, "No videos match this channel")
    order = db.get_setting(conn, "channel_order", "random")
    played = {
        r["video_id"] for r in conn.execute(
            "SELECT video_id FROM channel_played WHERE channel_key = ?", (key,)
        ).fetchall()
    }
    remaining = [v for v in pool if v not in played]
    looped = False
    if not remaining:
        conn.execute("DELETE FROM channel_played WHERE channel_key = ?", (key,))
        remaining = list(pool)
        looped = True
    if exclude is not None and len(remaining) > 1 and exclude in remaining:
        remaining = [v for v in remaining if v != exclude]

    if order == "sequential":
        # pool is already ordered by rel_path. Continue after `exclude` (the
        # video that just finished) rather than restarting at the top.
        choice = remaining[0]
        if exclude is not None and exclude in pool:
            after = [v for v in pool[pool.index(exclude) + 1:] if v in remaining]
            if after:
                choice = after[0]
    else:
        choice = random.choice(remaining)
    conn.execute(
        "INSERT OR IGNORE INTO channel_played (channel_key, video_id, played_at) "
        "VALUES (?, ?, ?)",
        (key, choice, time.time()),
    )
    db.record_play(conn, choice)
    row = db.get_video(conn, choice)
    return {
        "video": serialize_video(conn, row, include_detail=True),
        "channel_key": key,
        "pool_size": len(pool),
        "remaining": len(remaining) - 1,
        "looped": looped,
    }


@app.get("/api/channel/next")
def api_channel_next(
    folder: str = Query(""),
    tags: str = Query("", description="tags to include, comma-separated"),
    not_tags: str = Query("", alias="exclude_tags",
                          description="tags to keep out, comma-separated"),
    mode: str = Query("any", description="any | all, applied to `tags`"),
    exclude: Optional[int] = Query(None, description="video id to avoid repeating"),
):
    tag_list, not_list = _csv(tags), _csv(not_tags)
    key = _channel_key(folder, tag_list, not_list, mode)
    with db.get_db() as conn:
        pool = _pool(conn, [folder] if folder else [], tag_list, not_list, mode)
        return _serve_next(conn, key, pool, exclude)


@app.post("/api/channel/reset")
def api_channel_reset(folder: str = Query(""), tags: str = Query(""),
                      not_tags: str = Query("", alias="exclude_tags"),
                      mode: str = Query("any")):
    key = _channel_key(folder, _csv(tags), _csv(not_tags), mode)
    with db.get_db() as conn:
        conn.execute("DELETE FROM channel_played WHERE channel_key = ?", (key,))
    return {"ok": True, "channel_key": key}


# ---- Saved (named) channels --------------------------------------------------

def _serialize_channel(conn, ch) -> dict:
    folders, tags, exclude, mode = _channel_rule(ch)
    return {
        "id": ch["id"], "name": ch["name"],
        "folders": folders, "tags": tags,
        "exclude_tags": exclude, "tag_mode": mode,
        "count": len(_pool(conn, folders, tags, exclude, mode)),
    }


@app.get("/api/channels")
def api_channels_list():
    with db.get_db() as conn:
        return {"channels": [_serialize_channel(conn, ch)
                             for ch in db.list_channels(conn)]}


def _channel_payload(payload: dict) -> tuple[str, list, list, list, str]:
    name = (payload.get("name") or "").strip()
    folders = [str(f) for f in (payload.get("folders") or [])]
    tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
    exclude = [str(t).strip() for t in (payload.get("exclude_tags") or [])
               if str(t).strip()]
    mode = "all" if payload.get("tag_mode") == "all" else "any"
    if not name:
        raise HTTPException(400, "Channel name required")
    if not folders and not tags:
        raise HTTPException(400, "Pick at least one folder or tag to include")
    # An excluded tag that is also included would empty the channel silently,
    # so say so rather than saving a rule that can never match anything.
    overlap = {t.lower() for t in tags} & {t.lower() for t in exclude}
    if overlap:
        raise HTTPException(
            400, f"{', '.join(sorted(overlap))} is both included and excluded")
    return name, folders, tags, exclude, mode


@app.post("/api/channels")
def api_channels_create(payload: dict = Body(...)):
    name, folders, tags, exclude, mode = _channel_payload(payload)
    with db.get_db() as conn:
        cid = db.create_channel(conn, name, folders, tags, exclude, mode)
    return {"id": cid}


@app.patch("/api/channels/{channel_id}")
def api_channels_update(channel_id: int, payload: dict = Body(...)):
    name, folders, tags, exclude, mode = _channel_payload(payload)
    with db.get_db() as conn:
        if not db.get_channel(conn, channel_id):
            raise HTTPException(404, "Channel not found")
        db.update_channel(conn, channel_id, name, folders, tags, exclude, mode)
        # The rule changed, so the old rotation no longer describes this channel.
        conn.execute("DELETE FROM channel_played WHERE channel_key = ?",
                     (f"saved:{channel_id}",))
        return _serialize_channel(conn, db.get_channel(conn, channel_id))


@app.post("/api/channels/preview")
def api_channels_preview(payload: dict = Body(default={})):
    """How many videos a channel rule would match, for the live count in the
    channel builder. Ids are returned too, capped, so the UI can show a sample
    without a second round trip."""
    folders = [str(f) for f in (payload.get("folders") or [])]
    tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
    exclude = [str(t).strip() for t in (payload.get("exclude_tags") or [])
               if str(t).strip()]
    mode = "all" if payload.get("tag_mode") == "all" else "any"
    with db.get_db() as conn:
        pool = _pool(conn, folders, tags, exclude, mode)
        rows = [r for vid in pool[:12] if (r := db.get_video(conn, vid)) is not None]
        sample = serialize_videos(conn, rows)
    return {"count": len(pool), "sample": sample}


@app.delete("/api/channels/{channel_id}")
def api_channels_delete(channel_id: int):
    with db.get_db() as conn:
        db.delete_channel(conn, channel_id)
        conn.execute(
            "DELETE FROM channel_played WHERE channel_key = ?", (f"saved:{channel_id}",)
        )
    return {"ok": True}


@app.get("/api/channels/{channel_id}/next")
def api_channels_next(channel_id: int, exclude: Optional[int] = Query(None)):
    with db.get_db() as conn:
        ch = db.get_channel(conn, channel_id)
        if not ch:
            raise HTTPException(404, "Channel not found")
        pool = _pool(conn, *_channel_rule(ch))
        data = _serve_next(conn, f"saved:{channel_id}", pool, exclude)
        data["name"] = ch["name"]
        return data


@app.post("/api/channels/{channel_id}/reset")
def api_channels_reset(channel_id: int):
    with db.get_db() as conn:
        conn.execute(
            "DELETE FROM channel_played WHERE channel_key = ?", (f"saved:{channel_id}",)
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Media: thumbnails + range-enabled streaming
# ---------------------------------------------------------------------------

@app.get("/api/thumb/{video_id}")
def api_thumb(video_id: int):
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row or not row["thumbnail"]:
            raise HTTPException(404, "No thumbnail")
        thumb = config.thumbnail_dir / row["thumbnail"]
    if not thumb.exists():
        raise HTTPException(404, "No thumbnail")
    return FileResponse(thumb, media_type="image/jpeg")


@app.get("/api/folder-image")
def api_folder_image(
    path: str = Query("", description="folder relative to root"),
    kind: str = Query("folder"),
    v: str = Query("", description="cache-busting version from /api/browse"),
):
    """folder.jpg/png (downscaled for the library tiles) or backdrop.jpg/png."""
    if kind not in folderart.ART_NAMES:
        raise HTTPException(400, "Unknown image kind")
    src = folderart.find(path).get(kind)
    if src is None:
        raise HTTPException(404, "No image")
    out = folderart.tile_image(src) if kind == "folder" else src
    # A URL carrying the version changes whenever the file does, so it can be
    # cached for good; a bare one has to be revalidated.
    cache = "private, max-age=31536000, immutable" if v else "no-cache"
    return FileResponse(
        out,
        media_type=folderart.MEDIA_TYPES.get(out.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": cache},
    )


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _safe_media_path(rel_path: str) -> Path:
    # Prevent path traversal: resolved file must stay inside root_path.
    candidate = (config.root_path / rel_path).resolve()
    if not candidate.is_relative_to(config.root_path.resolve()):
        raise HTTPException(403, "Forbidden")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(404, "File not found")
    return candidate


# mimetypes maps .ts to a Qt translation format and doesn't know .m2ts at all,
# so a transport stream would be served as text/... and refuse to play. Pin the
# container types we index rather than trusting the platform mime database,
# which also varies with whatever /etc/mime.types the host happens to have.
_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/mp4",  # QuickTime; browsers treat MOV as MP4 and this is
                          # better understood than video/quicktime
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".mts": "video/mp2t",
}


def _media_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _MEDIA_TYPES:
        return _MEDIA_TYPES[ext]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


@app.get("/api/stream/{video_id}")
def api_stream(video_id: int, request: Request):
    with db.get_db() as conn:
        row = db.get_video(conn, video_id)
        if not row:
            raise HTTPException(404, "Video not found")
        rel_path = row["rel_path"]
    path = _safe_media_path(rel_path)
    file_size = path.stat().st_size
    media_type = _media_type_for(path)

    range_header = request.headers.get("range")
    if range_header:
        m = RANGE_RE.match(range_header)
        if not m:
            raise HTTPException(416, "Invalid range")
        start = int(m.group(1)) if m.group(1) else 0
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        if start > end or start >= file_size:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        length = end - start + 1
        chunk = 1024 * 1024  # 1 MiB

        def iter_file():
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    data = fh.read(min(chunk, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            iter_file(), status_code=206, headers=headers, media_type=media_type
        )

    # No range: return whole file but advertise range support.
    return FileResponse(
        path, media_type=media_type, headers={"Accept-Ranges": "bytes"}
    )


# ---------------------------------------------------------------------------
# Static frontend (mounted last so /api/* wins)
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__V__", ASSET_VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=config.host, port=int(config.port), reload=False)
