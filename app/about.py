"""Project metadata and the update check.

Everything the About page shows comes from `about.json` at the project root (or
wherever LOCALCHANNELS_ABOUT points), so licensing, contact details and links
can be edited without touching code or rebuilding the image.

The update check asks the GitHub Releases API for the newest release of the
repo named in about.json. It only ever runs when the user presses the button —
the app never phones home on its own — and the answer is cached briefly so
repeated presses don't burn through GitHub's unauthenticated rate limit.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .config import ROOT_DIR, env

DEFAULTS = {
    "name": "LocalChannels",
    "version": "0.0.0",
    "tagline": "",
    "description": "",
    "license": {"name": "", "url": ""},
    "author": "",
    "homepage": "",
    "contact": {"email": "", "issues": ""},
    "repo": {"owner": "", "name": ""},
    "links": [],
    "credits": "",
}

UPDATE_CACHE_SECONDS = 600
_USER_AGENT = "LocalChannels-update-check"

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None}


def about_file_path() -> Path:
    return Path(env("ABOUT") or (ROOT_DIR / "about.json"))


def load() -> dict:
    """Project metadata: about.json layered over the defaults."""
    data = json.loads(json.dumps(DEFAULTS))  # deep copy
    path = about_file_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return data
    for key, value in raw.items():
        if key.startswith("_"):     # "_comment" and friends are editor notes
            continue
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return data


def version() -> str:
    return str(load().get("version") or DEFAULTS["version"])


def _parts(tag: str) -> tuple:
    """Loose semver parse: 'v1.2.3-beta' -> (1, 2, 3). Unparsable -> ()."""
    match = re.search(r"(\d+(?:\.\d+)*)", str(tag or ""))
    if not match:
        return ()
    return tuple(int(n) for n in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    a, b = _parts(candidate), _parts(current)
    if not a or not b:
        return False
    length = max(len(a), len(b))
    return a + (0,) * (length - len(a)) > b + (0,) * (length - len(b))


def check_for_updates(force: bool = False) -> dict:
    """Ask GitHub for the latest release. Never raises; reports errors inline."""
    info = load()
    current = str(info.get("version") or DEFAULTS["version"])
    repo = info.get("repo") or {}
    owner, name = (repo.get("owner") or "").strip(), (repo.get("name") or "").strip()

    result = {
        "current": current, "latest": None, "url": None, "published_at": None,
        "update_available": False, "checked_at": time.time(), "error": None,
    }
    if not owner or not name:
        result["error"] = "no_repo"     # about.json hasn't been filled in yet
        return result

    with _lock:
        cached = _cache["value"]
        if (not force and cached
                and time.time() - _cache["at"] < UPDATE_CACHE_SECONDS
                and cached.get("current") == current):
            return {**cached, "cached": True}

    url = f"https://api.github.com/repos/{owner}/{name}/releases/latest"
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": _USER_AGENT,
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result["error"] = {403: "rate_limited", 404: "not_found"}.get(exc.code, "http_error")
        return result
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        result["error"] = "unreachable"
        return result

    latest = payload.get("tag_name") or payload.get("name") or ""
    result.update(
        latest=latest,
        url=payload.get("html_url") or f"https://github.com/{owner}/{name}/releases",
        published_at=payload.get("published_at"),
        notes=(payload.get("body") or "")[:2000],
        update_available=is_newer(latest, current),
    )
    with _lock:
        _cache.update(at=time.time(), value=result)
    return result
