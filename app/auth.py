"""Shared-password protection, configured from Settings → General.

Everything lives in the settings table, so it can be changed at runtime and
survives container updates:

    auth_enabled        "1" / "0"
    auth_password_hash  pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>
    auth_session_days   how long a sign-in lasts (one of SESSION_DAY_OPTIONS)
    auth_secret         random per-install key that signs session cookies

`auth_enabled` / `password` in config.json (or LOCALCHANNELS_AUTH_ENABLED /
LOCALCHANNELS_PASSWORD) are only a starting point: they are imported once, the
first time the app starts without a stored password, and ignored after that.
LOCALCHANNELS_AUTH_RESET=true throws the stored values away at startup, which
is the way back in after a forgotten password.

A session cookie is "<issued-at>.<signature>". The signature covers the issue
time and is keyed on the secret plus the password hash, so changing the
password signs every other browser out. Expiry is checked on the server against
the *current* validity setting — shortening it takes effect at once instead of
waiting for cookies that were handed out under the old value.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

from . import db
from .config import _as_bool, config, env

SESSION_DAY_OPTIONS = (1, 7, 30, 90)
DEFAULT_SESSION_DAYS = 30
COOKIE_NAME = "vt_session"
MAX_PASSWORD_LENGTH = 1024

_PBKDF2_ITERATIONS = 200_000
_DAY = 60 * 60 * 24

# In-memory copy of the stored settings, so the middleware doesn't open the
# database for every request (each video seek is one). Refreshed on every save.
_STATE: dict = {"enabled": False, "hash": "", "days": DEFAULT_SESSION_DAYS, "key": b""}


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: Optional[bytes] = None,
                   iterations: int = _PBKDF2_ITERATIONS) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, _digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        candidate = _hash_password(password, bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, stored)


# ---------------------------------------------------------------------------
# Stored state
# ---------------------------------------------------------------------------

def _session_days(raw) -> int:
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SESSION_DAYS
    return days if days in SESSION_DAY_OPTIONS else DEFAULT_SESSION_DAYS


def _load(conn) -> None:
    pw_hash = db.get_setting(conn, "auth_password_hash", "") or ""
    secret = db.get_setting(conn, "auth_secret", "") or ""
    _STATE["hash"] = pw_hash
    # Never "on" without a password — that would lock everyone out.
    _STATE["enabled"] = db.get_setting(conn, "auth_enabled", "0") == "1" and bool(pw_hash)
    _STATE["days"] = _session_days(db.get_setting(conn, "auth_session_days"))
    _STATE["key"] = f"{secret}::{pw_hash}".encode("utf-8")


def init() -> None:
    """Load the stored settings, importing config.json / env values on first run."""
    with db.get_db() as conn:
        if _as_bool(env("AUTH_RESET") or ""):
            conn.execute("DELETE FROM settings WHERE key IN "
                         "('auth_enabled', 'auth_password_hash')")
            print("[localchannels] LOCALCHANNELS_AUTH_RESET is set — the saved "
                  "password was discarded. Remove the variable once you are back in.")

        if not db.get_setting(conn, "auth_secret"):
            db.set_setting(conn, "auth_secret", secrets.token_hex(32))

        if not db.get_setting(conn, "auth_password_hash"):
            if config.password:
                db.set_setting(conn, "auth_password_hash", _hash_password(str(config.password)))
                db.set_setting(conn, "auth_enabled", "1" if config.auth_enabled else "0")
                print("[localchannels] Imported the password from config/environment. "
                      "It is managed in Settings → General from here on.")
            elif config.auth_enabled:
                print("[localchannels] AUTH is enabled in config but no password is "
                      "set — protection stays off. Set one in Settings → General.")
        _load(conn)


def enabled() -> bool:
    return bool(_STATE["enabled"])


def status() -> dict:
    return {
        "enabled": enabled(),
        "has_password": bool(_STATE["hash"]),
        "session_days": _STATE["days"],
        "session_day_options": list(SESSION_DAY_OPTIONS),
    }


def check_password(password: str) -> bool:
    return bool(_STATE["hash"]) and _verify_password(password, _STATE["hash"])


def update(enabled: Optional[bool] = None, password: Optional[str] = None,
           session_days: Optional[int] = None) -> dict:
    """Apply any of the three settings. Raises ValueError with a message that
    is safe to show the user when the combination doesn't make sense."""
    if password is not None:
        if not password:
            raise ValueError("The password can't be empty")
        if len(password) > MAX_PASSWORD_LENGTH:
            raise ValueError(f"The password can't be longer than {MAX_PASSWORD_LENGTH} characters")
    if session_days is not None and session_days not in SESSION_DAY_OPTIONS:
        raise ValueError("Session validity must be one of: "
                         + ", ".join(f"{d}" for d in SESSION_DAY_OPTIONS) + " days")
    if enabled and password is None and not _STATE["hash"]:
        raise ValueError("Set a password before turning protection on")

    with db.get_db() as conn:
        if password is not None:
            db.set_setting(conn, "auth_password_hash", _hash_password(password))
        if session_days is not None:
            db.set_setting(conn, "auth_session_days", str(session_days))
        if enabled is not None:
            db.set_setting(conn, "auth_enabled", "1" if enabled else "0")
        _load(conn)
    return status()


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------

def _sign(issued: str) -> str:
    return hmac.new(_STATE["key"], issued.encode("ascii"), hashlib.sha256).hexdigest()


def new_session() -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sign(issued)}"


def valid_session(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    issued, _, signature = token.partition(".")
    if not issued.isascii() or not issued.isdigit():
        return False
    if not hmac.compare_digest(_sign(issued), signature):
        return False
    age = time.time() - int(issued)
    return -60 <= age < _STATE["days"] * _DAY


def set_cookie(response) -> None:
    """Sign the browser in (or refresh its session) on the given response."""
    response.set_cookie(
        COOKIE_NAME, new_session(), httponly=True, samesite="lax",
        max_age=_STATE["days"] * _DAY, path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
