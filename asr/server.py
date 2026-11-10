"""Starts and checks WhisperKit's local transcription server.

`whisperkit-cli serve` keeps the speech model loaded, so each clip takes a
fraction of a second instead of several seconds. It listens on localhost only
and is pointed at the local model folder, so it never needs the network.
"""

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import config

_started: subprocess.Popen | None = None


def url(path: str = "") -> str:
    return f"http://localhost:{config.WHISPERKIT_PORT}{path}"


def is_running() -> bool:
    try:
        with urllib.request.urlopen(url("/health"), timeout=1.5) as response:
            return json.load(response).get("status") == "ok"
    except Exception:
        return False


def problems() -> list[str]:
    """Reasons the server cannot be started (empty list = fine)."""
    found = []
    if shutil.which("whisperkit-cli") is None:
        found.append("whisperkit-cli is not installed (brew install whisperkit-cli)")
    if not Path(config.WHISPERKIT_MODEL_PATH).exists():
        found.append(f"WhisperKit model not found at {config.WHISPERKIT_MODEL_PATH}")
    return found


def ensure_running(wait_s: float = 90.0) -> tuple[bool, str]:
    """Start the server if it is not already up. Returns (running, message)."""
    global _started
    if is_running():
        return True, "already running"
    missing = problems()
    if missing:
        return False, "; ".join(missing)
    config.LOG_DIR.mkdir(exist_ok=True)
    log = open(config.LOG_DIR / "whisperkit_server.log", "a")
    _started = subprocess.Popen(
        ["whisperkit-cli", "serve", "--model-path", str(config.WHISPERKIT_MODEL_PATH),
         "--language", "en", "--skip-special-tokens", "--without-timestamps",
         "--port", str(config.WHISPERKIT_PORT)],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_running():
            return True, "started"
        if _started.poll() is not None:
            return False, f"whisperkit-cli serve stopped (see {config.LOG_DIR / 'whisperkit_server.log'})"
        time.sleep(0.5)
    return False, f"whisperkit-cli serve did not answer within {wait_s:.0f} s"


def stop() -> None:
    """Stop the server, but only if this program started it."""
    global _started
    if _started and _started.poll() is None:
        _started.terminate()
    _started = None
