"""AI video analysis: sample frames with ffmpeg, send them to an
OpenAI-compatible vision model (vLLM, OpenAI, LM Studio, llama.cpp, ...) and
store the returned summary on the video.

No extra Python dependencies — HTTP is done with the stdlib so the endpoint can
be anything that speaks the OpenAI /chat/completions protocol.
"""
from __future__ import annotations

import base64
import json
import re
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
    "ai_disable_thinking": "0",                  # ask the server to skip reasoning tokens
}

REQUEST_TIMEOUT = 600  # seconds for one analysis call — VLMs can be slow

# The connection test is a handful of tokens with no images. Letting it inherit
# the analysis timeout meant a wedged endpoint held the request open for ten
# minutes, which reads as "the app is broken" rather than "the endpoint is".
TEST_TIMEOUT = 45

# Reasoning models emit their scratchpad inline when the server has no reasoning
# parser configured. It is not part of the summary, so it is stripped.
_THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Drop <think> blocks, including one left unclosed by a token limit."""
    text = _THINK_RE.sub("", text or "")
    # An unterminated block means the model ran out of tokens mid-thought;
    # everything from the opening tag on is scratchpad, not answer.
    cut = re.search(r"<(think|thinking|reasoning)>", text, re.I)
    if cut:
        text = text[:cut.start()]
    return text.strip()

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


def _post_chat(settings: dict, messages: list, max_tokens: int = 1024,
               timeout: Optional[int] = None) -> str:
    """POST to /chat/completions and return the assistant message content."""
    url = chat_url(settings.get("ai_endpoint", ""))
    timeout = timeout or REQUEST_TIMEOUT
    payload = {
        "model": settings.get("ai_model") or "default",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    # vLLM passes this through to the chat template; it is what turns off Qwen3
    # style thinking. Off by default because a server that doesn't know the
    # field (OpenAI itself, for one) rejects the whole request.
    if str(settings.get("ai_disable_thinking", "0")) == "1":
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    headers = {"Content-Type": "application/json"}
    api_key = (settings.get("ai_api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        # 502/503/504 come from something in front of the model server, so name
        # that rather than let it read as a failure of the model itself.
        hint = ""
        if e.code in (502, 503, 504):
            hint = (" — that status comes from a proxy or load balancer in "
                    "front of the endpoint, not from the model. Check that the "
                    "server is up and that the proxy's read timeout is longer "
                    "than the model takes to answer.")
        raise AIError(f"{url} returned HTTP {e.code}: {detail or e.reason}{hint}")
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise AIError(f"No response from {url} within {timeout}s")
        raise AIError(f"Could not reach {url}: {reason}")
    except TimeoutError:
        # Raised directly (rather than wrapped in URLError) once the connection
        # is up and the server simply stops talking.
        raise AIError(f"No response from {url} within {timeout}s")
    except OSError as e:
        raise AIError(f"Request to {url} failed: {e}")
    except json.JSONDecodeError:
        raise AIError("AI endpoint returned invalid JSON")

    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError(f"Unexpected response shape: {str(data)[:300]}")
    if isinstance(content, list):  # some servers return content parts
        content = " ".join(
            p.get("text", "") for p in content if isinstance(p, dict)
        )
    content = strip_reasoning(content or "")
    if not content:
        # Distinguish the two ways of getting nothing back, because they need
        # opposite fixes: raise the token budget, or turn thinking off.
        finish = (choice or {}).get("finish_reason")
        reasoned = ((choice.get("message") or {}).get("reasoning_content") or "").strip()
        if finish == "length" or reasoned:
            raise AIError(
                f"The model used its whole {max_tokens}-token budget on "
                "reasoning and never produced an answer. Turn on \u201cSkip "
                "reasoning tokens\u201d below, or use a non-reasoning model."
            )
        raise AIError("Model returned an empty response")
    return content


def test_connection(settings: dict) -> str:
    """Cheap text-only round trip to verify endpoint/model/key. Returns the reply.

    The budget is generous for a one-word answer on purpose: a reasoning model
    asked for ten tokens spends all of them thinking and returns nothing, which
    looks identical to a broken endpoint.
    """
    return _post_chat(
        settings,
        [{"role": "user", "content": "Reply with the single word: OK"}],
        max_tokens=512,
        timeout=TEST_TIMEOUT,
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
