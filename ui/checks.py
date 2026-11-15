"""Checks before the demo, in plain English, each with what to do if it fails.

    .venv/bin/python -m ui.checks        just the checks
    scripts/start.sh                     the checks, then Cowork with the demo settings

Nothing here records, sends or changes anything: it only looks (the Brain check sends one tiny
request to LM Studio on this Mac).
"""

import json
import shutil
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str          # what was found
    fix: str = ""        # what to do if it failed
    needed: bool = True  # False: Cowork still starts without it (e.g. the camera)


def lm_studio() -> Check:
    try:
        with urllib.request.urlopen(config.LLM_BASE_URL + "/models", timeout=2) as reply:
            models = reply.read().decode()
    except Exception:
        return Check("LM Studio", False, "its server isn't answering",
                     "Open LM Studio, go to the Developer tab, load Bonsai (ternary-bonsai-2-4b) and switch the server on.")
    if config.LLM_MODEL not in models:
        return Check("LM Studio", False, "running, but Bonsai isn't loaded",
                     f"In LM Studio's Developer tab, load {config.LLM_MODEL}.")
    return Check("LM Studio", True, f"running, {config.LLM_MODEL} loaded")


def brain() -> Check:
    """A 1-second known-answer test: pick the right code from a 3-item list, with thinking off."""
    from repair import get_llm
    saved, config.LLM = config.LLM, "real"
    try:
        llm = get_llm()
        schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["P1", "P2", "P3"]}},
                  "required": ["choice"], "additionalProperties": False}
        messages = [{"role": "system", "content": "Choose the code whose sentence matches what was heard. "
                                                  "P1: Open Mail. P2: I need water. P3: Scroll down. "
                                                  'Reply with JSON only: {"choice": "P1"}'},
                    {"role": "user", "content": "Heard: i need some water please"}]
        start = time.perf_counter()
        reply = llm.complete(messages, schema, purpose="check")
        took = time.perf_counter() - start
        choice = json.loads(reply.text).get("choice")
    except Exception as error:
        return Check("Bonsai", False, f"the test request failed ({type(error).__name__})",
                     "Check that LM Studio's server is on and Bonsai is loaded, then try again.")
    finally:
        config.LLM = saved
    if reply.thinking_tokens:
        return Check("Bonsai", False, f"it spent {reply.thinking_tokens} tokens thinking",
                     "Thinking must be off: in LM Studio, turn off reasoning/thinking for Bonsai.")
    if choice != "P2":
        return Check("Bonsai", False, f"answered {choice!r} to a simple test (expected P2)",
                     "Reload Bonsai in LM Studio (Developer tab), then try again.")
    return Check("Bonsai", True, f"answered a test correctly in {took:.1f} s, no thinking")


def whisperkit() -> Check:
    from asr.server import problems
    found = problems()
    if found:
        return Check("WhisperKit (hearing)", False, "; ".join(found),
                     "Install it with: brew install whisperkit-cli, and download the small.en model (see README).")
    return Check("WhisperKit (hearing)", True, "installed, small.en model on this Mac")


def ttskit() -> Check:
    if not shutil.which("whisperkit-cli"):
        return Check("TTSKit (voice)", False, "whisperkit-cli is not installed", "brew install whisperkit-cli", needed=False)
    if not Path(config.TTSKIT_MODELS_PATH).exists():
        return Check("TTSKit (voice)", False, f"model not found at {config.TTSKIT_MODELS_PATH}",
                     "Download the TTSKit 0.6B model (see README). Cowork works without it, silently.", needed=False)
    from tts.lines import to_prepare
    from tts.real import RealTTS
    from repair.store import open_profile
    profile = open_profile(config.ROOT / "profiles" / "cowork_profile.json", config.ROOT / "profiles" / "cowork_base.json")
    voice = RealTTS(profile=profile)
    sentences = to_prepare(profile, [])
    ready = sum(voice.is_cached(s) for s in sentences)
    if ready < len(sentences):
        return Check("TTSKit (voice)", True, f"model on this Mac; {ready} of {len(sentences)} sentences ready "
                     "(the rest are made in the background)", "For instant speech: .venv/bin/python scripts/prepare_voice.py",
                     needed=False)
    return Check("TTSKit (voice)", True, f"model on this Mac; all {len(sentences)} demo sentences ready")


def camera() -> Check:
    try:
        names = config.camera_names()
    except Exception as error:
        return Check("Camera", False, f"couldn't list cameras ({error})", "Hand answers will be off; voice and keys work.",
                     needed=False)
    if config.CAMERA_NAME in names:
        return Check("Camera", True, f"the built-in {config.CAMERA_NAME} is there")
    return Check("Camera", False, f"the built-in {config.CAMERA_NAME} wasn't found (seen: {', '.join(names) or 'none'})",
                 "Hand answers will be off; voice and keys still work. No other camera is ever used.", needed=False)


def microphone() -> Check:
    try:
        import AVFoundation as AV
        status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
        devices = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeAudio)
    except Exception as error:
        return Check("Microphone", False, f"couldn't check ({error})", "", needed=False)
    if not devices:
        return Check("Microphone", False, "no microphone found", "Use the test clips or type instead.", needed=False)
    if status == 2:   # denied
        return Check("Microphone", False, "macOS has not allowed Terminal to use it",
                     "System Settings > Privacy & Security > Microphone: turn on Terminal.", needed=False)
    return Check("Microphone", True, f"{devices[0].localizedName()}" + ("" if status == 3 else " (macOS may ask the first time)"))


def accessibility() -> Check:
    from actions.mac import ACCESSIBILITY_FIX, accessibility_allowed
    if accessibility_allowed():
        return Check("Accessibility", True, "allowed (Cowork can press keys and read the screen)")
    return Check("Accessibility", False, "not allowed", ACCESSIBILITY_FIX, needed=False)


def wifi() -> Check:
    from sysinfo import has_network_route, wifi_is_on
    on, route = wifi_is_on(), has_network_route()
    if on is False and not route:
        return Check("Wi-Fi", True, "off: fully offline")
    return Check("Wi-Fi", True, ("on" if on else "off, but another network is connected") +
                 " (Cowork never uses it; turn Wi-Fi off to show it is offline)", needed=False)


def run_all(print_them: bool = True) -> list[Check]:
    found = []
    for check in (lm_studio, brain, whisperkit, ttskit, camera, microphone, accessibility, wifi):
        if check is brain and not found[0].ok:
            continue   # no point asking Bonsai if LM Studio isn't there
        result = check()
        found.append(result)
        if print_them:
            mark = "OK  " if result.ok else ("FAIL" if result.needed else "note")
            print(f"  {mark}  {result.name}: {result.detail}")
            if result.fix and not result.ok or (result.fix and result.ok and not result.needed):
                print(f"        -> {result.fix}")
    return found


def ready(found: list[Check]) -> bool:
    return all(c.ok for c in found if c.needed)


if __name__ == "__main__":
    print("Cowork checks:")
    results = run_all()
    print("\nReady for the demo." if ready(results) else "\nNot ready: fix the FAIL lines above first.")
