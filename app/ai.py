"""AI video analysis: sample frames with ffmpeg, send them to an
OpenAI-compatible vision model (vLLM, OpenAI, LM Studio, llama.cpp, ...) and
store the returned summary on the video.

No extra Python dependencies — HTTP is done with the stdlib so the endpoint can
be anything that speaks the OpenAI /chat/completions protocol.
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from .config import config
from . import db

DEFAULT_SYSTEM_PROMPT = (
    "You are a video analyst. You receive a sequence of frames sampled in "
    "chronological order from a single video, plus its filename and duration. "
    "Write a concise, factual summary (2-4 sentences) of what the video shows: "
    "the setting, the people or subjects, what happens over time, and anything "
    "notable. Do not describe the frames individually and do not mention that "
    "you are looking at frames — describe the video as a whole."
)

# Settings (stored as strings in the settings table, like everything else).
AI_SETTING_DEFAULTS = {
    "ai_endpoint": "http://localhost:8000/v1",  # OpenAI-compatible base URL
    "ai_api_key": "",                            # optional (vLLM often needs none)
    "ai_model": "",                              # model name passed to the API
    "ai_system_prompt": DEFAULT_SYSTEM_PROMPT,
    "ai_frame_mode": "count",                    # count | interval
    "ai_frame_count": "8",                       # frames per video (count mode)
    "ai_frame_interval": "60",                   # seconds between frames (interval mode)
    "ai_max_frames": "16",                       # hard cap either way
    "ai_frame_width": "512",                     # frames are downscaled to this width
}

REQUEST_TIMEOUT = 600  # seconds for one model call — VLMs can be slow

# Module-level job progress the API can poll (same pattern as indexer.STATUS).
STATUS = {
    "running": False,
    "done": 0,
    "total": 0,
    "current": "",            # filename being analysed
    "current_video_id": None,
    "errors": 0,
    "last_error": "",
    "finished_at": 0.0,
}
_CANCEL = threading.Event()


class AIError(Exception):
    """Raised when analysis of a single video fails."""


def get_ai_settings(conn) -> dict:
    stored = db.all_settings(conn)
    return {k: stored.get(k, v) for k, v in AI_SETTING_DEFAULTS.items()}


def _num(value, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def sample_times(duration: Optional[float], settings: dict) -> list[float]:
    """Timestamps (seconds) at which to grab frames, per the configured mode."""
    max_frames = int(_num(settings.get("ai_max_frames"), 16, 1))
    if not duration or duration <= 0:
        return [1.0]
    if settings.get("ai_frame_mode") == "interval":
        interval = _num(settings.get("ai_frame_interval"), 60, 1)
        times: list[float] = []
        t = min(interval / 2.0, duration / 2.0)
        while t < duration and len(times) < max_frames:
            times.append(t)
            t += interval
        return times or [duration * 0.5]
    n = min(int(_num(settings.get("ai_frame_count"), 8, 1)), max_frames)
    # Midpoints of n equal segments: even coverage, avoids black first frames
    # and credits at the very end.
    return [duration * (i + 0.5) / n for i in range(n)]


def extract_frames(path: Path, duration: Optional[float], settings: dict) -> list[bytes]:
    """Grab JPEG frames from the video at the sampled timestamps."""
    width = int(_num(settings.get("ai_frame_width"), 512, 64))
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="vt-ai-") as td:
        for i, t in enumerate(sample_times(duration, settings)):
            out = Path(td) / f"frame{i:03d}.jpg"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{t:.3f}", "-i", str(path),
                "-frames:v", "1",
                "-vf", f"scale={width}:-2",
                "-q:v", "4", str(out),
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=120, check=False)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            if out.exists() and out.stat().st_size > 0:
                frames.append(out.read_bytes())
    return frames


# ---------------------------------------------------------------------------
# OpenAI-compatible API call
# ---------------------------------------------------------------------------

def chat_url(endpoint: str) -> str:
    e = (endpoint or "").strip().rstrip("/")
    if not e:
        raise AIError("AI endpoint is not configured (Settings → AI Analysis)")
    if e.endswith("/chat/completions"):
        return e
    return e + "/chat/completions"


def _post_chat(settings: dict, messages: list, max_tokens: int = 1024) -> str:
    """POST to /chat/completions and return the assistant message content."""
    url = chat_url(settings.get("ai_endpoint", ""))
    payload = {
        "model": settings.get("ai_model") or "default",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    api_key = (settings.get("ai_api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise AIError(f"API returned HTTP {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise AIError(f"Could not reach AI endpoint: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise AIError(f"Request failed: {e}")
    except json.JSONDecodeError:
        raise AIError("AI endpoint returned invalid JSON")

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected response shape: {str(data)[:300]}")
    if isinstance(content, list):  # some servers return content parts
        content = " ".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    content = (content or "").strip()
    if not content:
        raise AIError("Model returned an empty response")
    return content


def test_connection(settings: dict) -> str:
    """Cheap text-only round trip to verify endpoint/model/key. Returns the reply."""
    return _post_chat(
        settings,
        [{"role": "user", "content": "Reply with the single word: OK"}],
        max_tokens=10,
    )


def _fmt_duration(sec: Optional[float]) -> str:
    if not sec:
        return "unknown length"
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def analyze_video(row, settings: dict) -> str:
    """Analyse one video row and return the summary text. Raises AIError."""
    path = config.root_path / row["rel_path"]
    if not path.exists():
        raise AIError("File not found on disk")
    frames = extract_frames(path, row["duration"], settings)
    if not frames:
        raise AIError("Could not extract any frames (is ffmpeg available?)")

    user_content: list[dict] = [{
        "type": "text",
        "text": (
            f"These {len(frames)} frames were sampled in chronological order "
            f"from the video \"{row['filename']}\" "
            f"(duration {_fmt_duration(row['duration'])}). "
            "Summarize the video."
        ),
    }]
    for jpg in frames:
        b64 = base64.b64encode(jpg).decode("ascii")
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    messages = [
        {"role": "system", "content": settings.get("ai_system_prompt")
            or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _post_chat(settings, messages)


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def cancel_job() -> None:
    _CANCEL.set()


def run_job(video_ids: list[int]) -> None:
    """Analyse a list of videos sequentially, updating STATUS as it goes.
    Settings are snapshotted once at the start of the job."""
    STATUS.update(
        running=True, done=0, total=len(video_ids), current="",
        current_video_id=None, errors=0, last_error="", finished_at=0.0,
    )
    _CANCEL.clear()
    try:
        with db.get_db() as conn:
            settings = get_ai_settings(conn)
        model = settings.get("ai_model") or "default"
        for vid in video_ids:
            if _CANCEL.is_set():
                break
            with db.get_db() as conn:
                row = db.get_video(conn, vid)
            if not row:
                STATUS["done"] += 1
                continue
            STATUS["current"] = row["filename"]
            STATUS["current_video_id"] = vid
            try:
                summary = analyze_video(row, settings)
                with db.get_db() as conn:
                    db.set_ai_summary(conn, vid, summary, model)
            except AIError as e:
                STATUS["errors"] += 1
                STATUS["last_error"] = f"{row['filename']}: {e}"
            except Exception as e:  # never let one video kill the batch
                STATUS["errors"] += 1
                STATUS["last_error"] = f"{row['filename']}: {e}"
            STATUS["done"] += 1
    finally:
        STATUS.update(
            running=False, current="", current_video_id=None,
            finished_at=time.time(),
        )
