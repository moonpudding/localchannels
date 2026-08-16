"""Configuration loading for LocalChannels.

Reads config.json from the project root (or a path given by the
LOCALCHANNELS_CONFIG env var). Everything has a sensible default so a missing
key never crashes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# Project root is the parent of the `app` package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "site_name": "LocalChannels",
    "root_path": str(ROOT_DIR / "videos"),
    "data_dir": str(ROOT_DIR / "data"),
    "host": "127.0.0.1",
    "port": 8000,
    "default_theme": "mocha",
    "thumbnail_width": 480,
    # Fraction of the video's duration at which the poster frame is grabbed.
    "thumbnail_seek_fraction": 0.20,
    # Simple shared-password protection (off by default).
    "auth_enabled": False,
    "password": "",
    "video_extensions": [
        ".mp4", ".mkv", ".webm", ".avi", ".mov",
        ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
    ],
}


class Config:
    def __init__(self, data: dict):
        self._data = data
        for key, value in DEFAULTS.items():
            self._data.setdefault(key, value)
        # Normalise paths and derived locations.
        self.root_path = Path(self._data["root_path"]).expanduser().resolve()
        self.data_dir = Path(self._data["data_dir"]).expanduser().resolve()
        self.thumbnail_dir = self.data_dir / "thumbnails"
        self.db_path = self.data_dir / "library.db"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        # Extensions stored lowercase with a leading dot.
        self.video_extensions = {
            e.lower() if e.startswith(".") else "." + e.lower()
            for e in self._data["video_extensions"]
        }

    def __getattr__(self, item):
        # Fall through to raw config values (site_name, host, port, etc.).
        try:
            return self._data[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def public(self) -> dict:
        """Config values that are safe to expose to the browser."""
        return {
            "site_name": self._data["site_name"],
            "default_theme": self._data["default_theme"],
            "root_path": str(self.root_path),
            "auth_enabled": bool(self._data["auth_enabled"]),
        }


def _as_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# Environment variables that override config.json values (handy for Docker).
# Format: SUFFIX -> (config_key, caster), read as LOCALCHANNELS_<SUFFIX>.
ENV_PREFIX = "LOCALCHANNELS_"
ENV_OVERRIDES = {
    "SITE_NAME": ("site_name", str),
    "ROOT_PATH": ("root_path", str),
    "DATA_DIR": ("data_dir", str),
    "HOST": ("host", str),
    "PORT": ("port", int),
    "DEFAULT_THEME": ("default_theme", str),
    "THUMBNAIL_WIDTH": ("thumbnail_width", int),
    "THUMBNAIL_SEEK_FRACTION": ("thumbnail_seek_fraction", float),
    "AUTH_ENABLED": ("auth_enabled", _as_bool),
    "PASSWORD": ("password", str),
}


def env(suffix: str) -> Optional[str]:
    """Read the LOCALCHANNELS_<suffix> environment variable."""
    return os.environ.get(ENV_PREFIX + suffix) or None


def _apply_env(data: dict) -> dict:
    for suffix, (key, caster) in ENV_OVERRIDES.items():
        raw = env(suffix)
        if raw is not None and raw != "":
            try:
                data[key] = caster(raw)
            except ValueError:
                pass  # ignore malformed env values, keep existing/default
    # Comma-separated list of extensions, e.g. ".mp4,.mkv,.webm"
    exts = env("VIDEO_EXTENSIONS")
    if exts:
        data["video_extensions"] = [e.strip() for e in exts.split(",") if e.strip()]
    return data


def config_file_path() -> Path:
    """Location of config.json (LOCALCHANNELS_CONFIG wins, else project root)."""
    return Path(env("CONFIG") or (ROOT_DIR / "config.json"))


def load_config() -> Config:
    path = config_file_path()
    data: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    data = _apply_env(data)  # env vars win over config.json
    return Config(data)


config = load_config()
