"""Second Voice settings. Change the switches below; nothing else needs editing.

Each AI part can be "fake" (no model needed, for building and testing)
or "real" (uses the actual model on this Mac).
"""

from pathlib import Path

# ---- Switches -------------------------------------------------------------
ASR = "fake"    # speech-to-text:  "fake" or "real"
LLM = "real"    # intent repair:   "fake" or "real"
TTS = "fake"    # text-to-speech:  "fake" or "real"

CAMERA = "builtin"  # always the MacBook's own camera, never an iPhone

# ---- Details (rarely changed) ---------------------------------------------
ROOT = Path(__file__).resolve().parent
BASE_PROFILE_PATH = ROOT / "profiles" / "sample_profile.json"   # the original; never changed
PROFILE_PATH = ROOT / "profiles" / "profile.json"   # working copy: learning goes here
PROFILE_BACKUP_DIR = ROOT / "profiles" / "backups"  # --fresh saves the old working copy here
GESTURES_PATH = ROOT / "profiles" / "gestures.json"   # made by hand calibration
LOG_DIR = ROOT / "logs"

LLM_BASE_URL = "http://localhost:1234/v1"   # LM Studio local server
LLM_MODEL = "ternary-bonsai-2-4b"
LLM_TIMEOUT_S = 60
LLM_MAX_TOKENS = 150   # a normal answer is about 65-75 tokens

# Learning and options
FAST_PATH_SIMILARITY = 90   # % similar to a learned example to skip the LLM
FAST_PATH_MIN_CHARS = 5     # shorter than this must match a learned example exactly
PROMPT_EXAMPLES = 10        # most recent learned examples shown to the LLM
SAME_PHRASE_SIMILARITY = 90 # a new sentence this similar to an existing phrase reuses it
MAX_GUESS_CHARS = 120       # "Something else" guesses are one short sentence

TTS_MODEL = "0.6b"          # Argmax TTSKit preset
TTS_TIMEOUT_S = 120


def pick_camera() -> int:
    """Return the OpenCV camera number of the MacBook's built-in camera.

    OpenCV numbers cameras in the same order as macOS's camera list, so we
    look through that list and pick the built-in one, skipping iPhones
    (Continuity Camera) and other external cameras.
    """
    if CAMERA != "builtin":
        raise ValueError(f'Unknown CAMERA setting "{CAMERA}" (expected "builtin")')
    import AVFoundation as AV

    devices = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)
    for index, device in enumerate(devices):
        is_builtin = device.deviceType() == AV.AVCaptureDeviceTypeBuiltInWideAngleCamera
        is_iphone = bool(getattr(device, "isContinuityCamera", lambda: False)())
        if is_builtin and not is_iphone:
            return index
    names = ", ".join(d.localizedName() for d in devices) or "none"
    raise RuntimeError(f"No built-in MacBook camera found (cameras seen: {names}).")
