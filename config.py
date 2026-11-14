"""Second Voice settings. Change the switches below; nothing else needs editing.

Each AI part can be "fake" (no model needed, for building and testing)
or "real" (uses the actual model on this Mac).
"""

from pathlib import Path

# ---- Switches -------------------------------------------------------------
ASR = "fake"    # speech-to-text:  "fake" or "real"
LLM = "real"    # intent repair:   "fake" or "real"
TTS = "fake"    # text-to-speech:  "fake" or "real"
ACTIONS = "fake"  # acting on the Mac: "fake" (nothing really happens) or "real"

CAMERA = "builtin"  # always the MacBook's own camera, never an iPhone
CAMERA_NAME = "FaceTime HD Camera"  # chosen by this exact name; no other camera is ever used

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
LLM_MAX_TOKENS = 80    # a normal answer is 11-25 tokens; a reply message can add about 30
GUESS_MAX_TOKENS = 120 # a free guess: one sentence plus up to 3 key words

# When may "high confidence" be trusted? (see repair/support.py and eval/calibrate.py)
HIGH_EVIDENCE = 0.7         # share of the heard words / the phrase that must agree to act without asking

SECOND_OPINION = True       # after a "no", ask the Brain again with that choice ruled out

# Listening again (see repair/confirm.py; thresholds measured in eval/relisten.py)
AUDIO_CONFIRM = True          # check the Brain's guess against the sound; if it fits, speak without asking
AUDIO_CONFIRM_MATCH = 0.8     # how much of the guess's words must come back
AUDIO_CONFIRM_LOGPROB = -0.3  # and how sure WhisperKit must be (closer to 0 = more sure)
RELISTEN_AFTER_NO = True      # after a "no", look through the phrases for one that fits the sound
RELISTEN_CHUNK = 12           # phrases hinted per pass
RELISTEN_MAX_S = 8.0          # stop looking after this long

# Learning and options
FAST_PATH_SIMILARITY = 90   # % similar to a learned example to skip the LLM
FAST_PATH_MIN_CHARS = 5     # shorter than this must match a learned example exactly
PROMPT_EXAMPLES = 10        # most recent learned examples shown to the LLM
SAME_PHRASE_SIMILARITY = 90 # a new sentence this similar to an existing phrase reuses it
MAX_GUESS_CHARS = 120       # "Something else" guesses are one short sentence
GUESS_AGAIN_TEMPERATURE = 0.8  # some randomness for guesses 2 and 3 (else it repeats itself)

# Speech recognition (WhisperKit local server; see asr/server.py)
HF_MODELS = Path.home() / "Documents" / "huggingface" / "models"
WHISPERKIT_MODEL_PATH = HF_MODELS / "argmaxinc" / "whisperkit-coreml" / "openai_whisper-small.en"
WHISPERKIT_PORT = 50060
ASR_TIMEOUT_S = 30

# The app's screen (ui/)
UI_PORT = 8765               # http://localhost:8765 (only reachable from this Mac)
MODEL_MEMORY_BUDGET_GB = 3.5 # all models together must stay under this (shown on the metrics panel)

# Acting on the Mac (see actions/mac.py)
EMAIL_REALLY_SEND = False   # False: a reply is prepared in a Mail window but NOT sent
MAX_APPS = 100              # how many app names the Brain may choose from (every installed app)
MAX_TARGETS = 25            # how many on-screen buttons/links the Brain may choose from
ACTION_TIMEOUT_S = 8        # no automation call may hang longer than this
CONTACTS_PATH = ROOT / "profiles" / "contacts.json"   # names -> email addresses, for "write an email to ..."
# Websites the Brain may choose from ("open YouTube"). Anything else becomes a Google search.
SITES = {
    "Google": "https://www.google.com",
    "YouTube": "https://www.youtube.com",
    "Gmail": "https://mail.google.com",
    "Wikipedia": "https://www.wikipedia.org",
    "BBC News": "https://www.bbc.com/news",
    "Weather": "https://weather.com",
    "Google Maps": "https://maps.google.com",
    "Amazon": "https://www.amazon.com",
    "Netflix": "https://www.netflix.com",
    "Spotify": "https://open.spotify.com",
    "Facebook": "https://www.facebook.com",
    "WhatsApp": "https://web.whatsapp.com",
    "ALS Association": "https://www.als.org",
    "Parkinson's Foundation": "https://www.parkinson.org",
}

# Speech synthesis (TTSKit; see tts/real.py)
TTS_MODEL = "0.6b"          # Argmax TTSKit preset (the 1.7b model does not fit in 8 GB)
TTS_VOICE = "aiden"         # aiden, ryan, serena, vivian, ...
TTS_NEW_SENTENCES = "ttskit"  # a sentence with no audio ready yet: "ttskit" (same voice, about 7 s) or "say" (instant, Mac voice)
TTS_TIMEOUT_S = 120
TTS_CACHE_DIR = ROOT / "cache" / "tts"   # ready-made audio for known phrases
TTSKIT_MODELS_PATH = HF_MODELS / "argmaxinc" / "ttskit-coreml"
TTSKIT_TOKENIZER_PATH = HF_MODELS / "Qwen" / "Qwen3-0.6B"


class CameraNotFound(RuntimeError):
    """The built-in camera isn't there. The message says so in plain words."""


def camera_names() -> list[str]:
    """Cameras macOS sees, in the order OpenCV numbers them (OpenCV asks macOS
    the same way: AVCaptureDevice devicesWithMediaType)."""
    import AVFoundation as AV
    return [str(d.localizedName()) for d in AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)]


def pick_camera() -> int:
    """The OpenCV camera number of the built-in "FaceTime HD Camera", chosen by
    name. An iPhone (Continuity Camera) or any other camera is never used: if the
    built-in camera isn't found, CameraNotFound is raised instead of falling back."""
    if CAMERA != "builtin":
        raise ValueError(f'Unknown CAMERA setting "{CAMERA}" (expected "builtin")')
    import AVFoundation as AV

    devices = AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo)
    for index, device in enumerate(devices):
        is_iphone = bool(getattr(device, "isContinuityCamera", lambda: False)())
        if str(device.localizedName()) == CAMERA_NAME and not is_iphone:
            return index
    seen = ", ".join(str(d.localizedName()) for d in devices) or "none"
    raise CameraNotFound(f'The built-in "{CAMERA_NAME}" was not found, so the camera is off '
                         f"(cameras seen: {seen}). No other camera is used. Hand answers are off; "
                         "voice and keyboard still work.")
