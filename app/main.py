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
from . import about, ai, backup, db, indexer

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
# ---------------------------------------------------------------------------
AUTH_ENABLED = bool(config.auth_enabled) and bool(config.password)
if config.auth_enabled and not config.password:
    print("[localchannels] AUTH is enabled but no password is set — auth "
          "disabled. Set LOCALCHANNELS_PASSWORD to protect the site.")

# Session cookie value derived from the password: stable across restarts, and
# doesn't expose the password itself.
_AUTH_TOKEN = hashlib.sha256(
    ("localchannels-session::" + str(config.password)).encode("utf-8")
).hexdigest()
COOKIE_NAME = "vt_session"
PUBLIC_PATHS = {"/login", "/api/login", "/api/logout", "/api/health"}


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    if not AUTH_ENABLED:
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATHS or request.cookies.get(COOKIE_NAME) == _AUTH_TOKEN:
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return RedirectResponse("/login")


@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.post("/api/login")
def api_login(payload: dict = Body(...)):
    if not AUTH_ENABLED:
        return {"ok": True}
    if str(payload.get("password", "")) == str(config.password):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            COOKIE_NAME, _AUTH_TOKEN, httponly=True, samesite="lax",
            max_age=60 * 60 * 24 * 30, path="/",
        )
        return resp
    return JSONResponse({"detail": "Incorrect password"}, status_code=401)


@app.post("/api/logout")
def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
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
    if not AUTH_ENABLED:
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
        threading.Thread(target=indexer.reindex, daemon=True).start()


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
            db.get_setting(conn, "filename_tag_brackets", "square"),
        )
    return _TITLE_CFG["value"]


def display_title(conn, filename: str) -> str:
    """Filename without its extension, and without the bracketed tag section
    when title stripping is on. The file on disk is never renamed."""
    stem = os.path.splitext(filename)[0]
    strip, kind = _title_config(conn)
    return indexer.strip_filename_tags(stem, kind) if strip else stem


def serialize_video(conn, row, include_detail: bool = False) -> dict:
    d = {
        "id": row["id"],
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
        "last_played": db.last_played(conn, row["id"]),
        "play_count": db.play_count(conn, row["id"]),
    }
    if include_detail:
        d["tags"] = db.tags_for_video(conn, row["id"])
        d["indexed_at"] = row["indexed_at"]
        d["ai_summary"] = row["ai_summary"]
        d["ai_summary_at"] = row["ai_summary_at"]
        d["ai_model"] = row["ai_model"]
    return d


# ---------------------------------------------------------------------------
# Config + indexing
# ---------------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    return config.public()


# Server-side settings the UI can change, with their defaults.
SETTING_DEFAULTS = {
    "filename_tag_brackets": "square",   # square | round | curly
    "filename_tags_auto": "0",           # "1" to auto-tag on every scan
    "filename_tags_strip_title": "1",    # "1" to hide the tag section in titles
    "backup_include_thumbs": "0",        # "1" to put cached thumbnails in backups
    **ai.AI_SETTING_DEFAULTS,            # ai_endpoint, ai_model, ai_frame_mode, ...
}

# Settings stored as "0"/"1"; anything truthy-looking is normalised on save.
_BOOL_SETTINGS = {"filename_tags_auto", "filename_tags_strip_title",
                  "backup_include_thumbs"}

# Saving any of these changes how titles are derived, so drop the cache.
_TITLE_SETTINGS = {"filename_tags_strip_title", "filename_tag_brackets"}

# Numeric AI settings and their (minimum, maximum) sanity bounds.
_AI_NUMERIC_BOUNDS = {
    "ai_frame_count": (1, 64),
    "ai_frame_interval": (1, 86400),
    "ai_max_frames": (1, 64),
    "ai_frame_width": (64, 4096),
}


@app.get("/api/settings")
def api_get_settings():
    with db.get_db() as conn:
        stored = db.all_settings(conn)
    return {**SETTING_DEFAULTS, **stored}


@app.post("/api/settings")
def api_set_setting(payload: dict = Body(...)):
    key = payload.get("key")
    value = payload.get("value")
    if key not in SETTING_DEFAULTS:
        raise HTTPException(400, "Unknown setting")
    if key == "filename_tag_brackets" and value not in indexer.FILENAME_TAG_BRACKETS:
        raise HTTPException(400, "Invalid bracket type")
    if key in _BOOL_SETTINGS:
        value = "1" if str(value).strip().lower() in ("1", "true", "yes", "on") else "0"
    if key == "ai_frame_mode" and value not in ("count", "interval"):
        raise HTTPException(400, "Frame mode must be 'count' or 'interval'")
    if key in _AI_NUMERIC_BOUNDS:
        lo, hi = _AI_NUMERIC_BOUNDS[key]
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} must be a number")
        if not (lo <= n <= hi):
            raise HTTPException(400, f"{key} must be between {lo} and {hi}")
        value = str(int(n))
    with db.get_db() as conn:
        db.set_setting(conn, key, str(value))
    if key in _TITLE_SETTINGS:
        _TITLE_CFG["value"] = None
    return {"ok": True, "key": key, "value": str(value)}


@app.post("/api/reindex")
def api_reindex(force: bool = Query(False)):
    if indexer.STATUS["running"]:
        return {"status": "already_running", **indexer.STATUS}
    threading.Thread(target=indexer.reindex, kwargs={"force": force}, daemon=True).start()
    return {"status": "started"}


@app.get("/api/index/status")
def api_index_status():
    return indexer.STATUS


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
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
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
                    f"SELECT id FROM videos WHERE (folder = ? OR folder LIKE ?) "
                    f"{skip} ORDER BY rel_path",
                    (folder, f"{folder}/%"),
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
    threading.Thread(target=ai.run_job, args=(ids,), daemon=True).start()
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

def _file_hash(conn, row) -> Optional[str]:
    """SHA-256 of the file's contents, cached in the DB and recomputed only when
    the file's mtime changes. Returns None if the file is missing/unreadable."""
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

    method=hash     — byte-identical files (exact SHA-256 match; size-prefiltered)
    method=size     — same exact file size
    method=duration — same runtime, compared at millisecond resolution.
                      `tolerance_ms` widens the match window (0 = exact to the
                      millisecond, which is what you want for a large library —
                      second-level rounding groups thousands of unrelated clips).
    """
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM videos").fetchall()
        groups: list[dict] = []

        def emit(items):
            if len(items) < 2:
                return
            durations = [it["duration"] for it in items if it["duration"] is not None]
            groups.append({
                "videos": [serialize_video(conn, it) for it in items],
                "size": items[0]["size"],
                "duration": items[0]["duration"],
                "duration_min": min(durations) if durations else None,
                "duration_max": max(durations) if durations else None,
            })

        if method == "size":
            buckets: dict = {}
            for r in rows:
                if r["size"]:
                    buckets.setdefault(r["size"], []).append(r)
            for items in buckets.values():
                emit(items)
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
                    emit(run)
                    run = []
                run.append(r)
                prev_ms = ms
            emit(run)
        else:  # hash — prefilter by size, then confirm with content hash
            by_size: dict = {}
            for r in rows:
                if r["size"]:
                    by_size.setdefault(r["size"], []).append(r)
            for items in by_size.values():
                if len(items) < 2:
                    continue
                by_hash: dict = {}
                for it in items:
                    digest = _file_hash(conn, it)
                    if digest:
                        by_hash.setdefault(digest, []).append(it)
                for dups in by_hash.values():
                    emit(dups)

    groups.sort(key=lambda g: (g["size"] or 0) * len(g["videos"]), reverse=True)
    return {"method": method, "tolerance_ms": tolerance_ms, "groups": groups}


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
        db.delete_video(conn, video_id)
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
        db.delete_video(conn, video_id)
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
            db.delete_video(conn, vid)
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
        # Subfolders: distinct folder values that are direct children of `path`.
        rows = conn.execute("SELECT DISTINCT folder FROM videos").fetchall()
        prefix = f"{path}/" if path else ""
        subfolders: dict[str, int] = {}
        for r in rows:
            folder = r["folder"]
            if path:
                if not folder.startswith(prefix) and folder != path:
                    continue
                remainder = folder[len(prefix):] if folder.startswith(prefix) else ""
            else:
                remainder = folder
            if remainder == "":
                continue
            child = remainder.split("/")[0]
            child_full = f"{prefix}{child}"
            subfolders.setdefault(child_full, 0)

        # Count videos under each subfolder (recursive).
        for child_full in list(subfolders.keys()):
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM videos WHERE folder = ? OR folder LIKE ?",
                (child_full, f"{child_full}/%"),
            ).fetchone()["c"]
            subfolders[child_full] = cnt

        # Videos directly in this folder, sorted per the global preference
        # (NULLs always last so missing durations/dates don't jump to the top).
        vrows = conn.execute(
            f"SELECT * FROM videos WHERE folder = ? "
            f"ORDER BY ({col} IS NULL), {col} {direction}",
            (path,),
        ).fetchall()
        videos = [serialize_video(conn, v) for v in vrows]

    folders = [
        {"path": p, "name": p.split("/")[-1], "count": c}
        for p, c in sorted(subfolders.items())
    ]
    return {"path": path, "folders": folders, "videos": videos}


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
        videos = [serialize_video(conn, v) for v in rows]
    return {"videos": videos, "count": total, "limit": limit, "offset": offset}


@app.get("/api/vr")
def api_vr_videos(sort: str = Query("name"), order: str = Query("asc")):
    col = _sort_column(sort)
    direction = "DESC" if str(order).lower() == "desc" else "ASC"
    with db.get_db() as conn:
        rows = db.vr_videos(conn, f"({col} IS NULL), {col} {direction}")
        videos = [serialize_video(conn, v) for v in rows]
    return {"videos": videos, "count": len(videos)}


@app.get("/api/stats")
def api_stats():
    with db.get_db() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM videos").fetchone()["c"]
        vr = db.count_vr(conn)
    return {"total": total, "vr": vr}


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
        rows = conn.execute("SELECT DISTINCT folder FROM videos").fetchall()
    folders = set()
    for r in rows:
        parts = r["folder"].split("/") if r["folder"] else []
        acc = ""
        for part in parts:
            acc = f"{acc}/{part}" if acc else part
            folders.add(acc)
    with db.get_db() as conn:
        result = []
        for f in sorted(folders):
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM videos WHERE folder = ? OR folder LIKE ?",
                (f, f"{f}/%"),
            ).fetchone()["c"]
            result.append({"path": f, "name": f.split("/")[-1],
                           "depth": f.count("/"), "count": cnt})
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
        out = []
        for r in rows:
            v = serialize_video(conn, r)
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
        videos = [serialize_video(conn, r) for r in rows]
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
        videos = [serialize_video(conn, r) for r in rows]
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


def _plan_tag_writes(conn) -> list[dict]:
    """Work out the rename each tagged video would need, without touching disk.

    Videos with no tags are left alone entirely — this writes tags out, it never
    strips a bracket section off a file the app doesn't have tags for.
    """
    kind = db.get_setting(conn, "filename_tag_brackets", "square")
    open_c, close_c = indexer.FILENAME_TAG_BRACKETS.get(
        kind, indexer.FILENAME_TAG_BRACKETS["square"]
    )
    rows = conn.execute("SELECT * FROM videos ORDER BY rel_path").fetchall()

    plan: list[dict] = []
    claimed: dict[str, int] = {}       # target rel_path -> video id that wants it
    for r in rows:
        tags = db.tags_for_video(conn, r["id"])
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

        if new_name == r["filename"]:
            item.update(status="unchanged")
        elif new_rel in claimed:
            item.update(status="skipped",
                        reason="another video would be renamed to this same name")
        elif (config.root_path / new_rel).exists():
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
        videos = [serialize_video(conn, r) for r in rows]
    return {"videos": videos, "total": len(videos)}


@app.get("/api/tag/{name}")
def api_tag_videos(name: str):
    with db.get_db() as conn:
        rows = db.videos_for_tag(conn, name)
        videos = [serialize_video(conn, r) for r in rows]
    return {"tag": name, "videos": videos}


# ---------------------------------------------------------------------------
# Channel mode: shuffle-without-replacement over a folder (+subfolders)/tags
# ---------------------------------------------------------------------------

def _channel_key(folder: str, tags: list[str]) -> str:
    raw = folder + "|" + ",".join(sorted(t.lower() for t in tags))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pool(conn, folders: list[str], tags: list[str]) -> list[int]:
    """Video ids for a channel: the UNION of videos in any of the folders
    (incl. subfolders) and videos with any of the tags. A "" folder entry means
    the whole library."""
    raw = [f.strip("/") for f in (folders or [])]
    whole_library = "" in raw
    real_folders = [f for f in raw if f]
    tags = [t for t in (tags or []) if t.strip()]

    if whole_library:
        return [r["id"] for r in conn.execute("SELECT id FROM videos").fetchall()]

    clauses: list = []
    params: list = []
    if real_folders:
        subs = []
        for f in real_folders:
            subs.append("(folder = ? OR folder LIKE ?)")
            params.extend([f, f"{f}/%"])
        clauses.append("(" + " OR ".join(subs) + ")")
    if tags:
        ph = ", ".join("?" for _ in tags)
        clauses.append(
            "id IN (SELECT vt.video_id FROM video_tags vt "
            f"JOIN tags t ON t.id = vt.tag_id WHERE LOWER(t.name) IN ({ph}))"
        )
        params.extend([t.lower() for t in tags])
    if not clauses:
        return []
    sql = "SELECT id FROM videos WHERE " + " OR ".join(clauses)
    return [r["id"] for r in conn.execute(sql, params).fetchall()]


def _serve_next(conn, key: str, pool: list[int], exclude: Optional[int]) -> dict:
    """Shuffle-without-replacement pick from a pool, looping when exhausted."""
    if not pool:
        raise HTTPException(404, "No videos match this channel")
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
    tags: str = Query(""),
    exclude: Optional[int] = Query(None, description="video id to avoid repeating"),
):
    tag_list = [t for t in (tags.split(",") if tags else []) if t.strip()]
    key = _channel_key(folder, tag_list)
    with db.get_db() as conn:
        pool = _pool(conn, [folder] if folder else [], tag_list)
        return _serve_next(conn, key, pool, exclude)


@app.post("/api/channel/reset")
def api_channel_reset(folder: str = Query(""), tags: str = Query("")):
    tag_list = [t for t in (tags.split(",") if tags else []) if t.strip()]
    key = _channel_key(folder, tag_list)
    with db.get_db() as conn:
        conn.execute("DELETE FROM channel_played WHERE channel_key = ?", (key,))
    return {"ok": True, "channel_key": key}


# ---- Saved (named) channels --------------------------------------------------

@app.get("/api/channels")
def api_channels_list():
    with db.get_db() as conn:
        out = []
        for ch in db.list_channels(conn):
            folders = json.loads(ch["folders"])
            tags = json.loads(ch["tags"])
            out.append({
                "id": ch["id"], "name": ch["name"],
                "folders": folders, "tags": tags,
                "count": len(_pool(conn, folders, tags)),
            })
    return {"channels": out}


@app.post("/api/channels")
def api_channels_create(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    folders = payload.get("folders") or []
    tags = payload.get("tags") or []
    if not name:
        raise HTTPException(400, "Channel name required")
    if not folders and not tags:
        raise HTTPException(400, "Pick at least one folder or tag")
    with db.get_db() as conn:
        cid = db.create_channel(conn, name, folders, tags)
    return {"id": cid}


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
        pool = _pool(conn, json.loads(ch["folders"]), json.loads(ch["tags"]))
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


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _safe_media_path(rel_path: str) -> Path:
    # Prevent path traversal: resolved file must stay inside root_path.
    candidate = (config.root_path / rel_path).resolve()
    if not str(candidate).startswith(str(config.root_path)):
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
