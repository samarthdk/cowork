"""The working speech profile: where everything learned is kept.

profiles/sample_profile.json  the base profile (committed, never changed by the app)
profiles/profile.json         the working copy, made from the base on first use;
                              new phrases and learned examples go here (personal,
                              not in git)
profiles/backups/             --fresh saves the old working copy here first
"""

import json
import re
import shutil
import time
from pathlib import Path

from rapidfuzz import fuzz

import config


def normalize(text: str) -> str:
    """Lowercase, no punctuation, single spaces: for fair similarity scores."""
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def similarity(a: str, b: str) -> float:
    """0-100: how alike two pieces of text are (after normalizing)."""
    return fuzz.ratio(normalize(a), normalize(b))


def open_profile(path=None, base=None) -> dict:
    """Load the working profile, creating it from the base profile if missing."""
    path, base = Path(path or config.PROFILE_PATH), Path(base or config.BASE_PROFILE_PATH)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base, path)
    profile = json.loads(path.read_text())
    profile.setdefault("examples", [])
    return profile


def save_profile(profile: dict, path=None) -> None:
    """Write safely: to a temporary file first, then swap it in."""
    path = Path(path or config.PROFILE_PATH)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    tmp.replace(path)


def fresh_profile(path=None, base=None, backup_dir=None) -> Path | None:
    """Start over from the base profile. The old working copy is backed up first.
    Returns the backup's path (None if there was nothing to back up)."""
    path, base = Path(path or config.PROFILE_PATH), Path(base or config.BASE_PROFILE_PATH)
    backup_dir = Path(backup_dir or config.PROFILE_BACKUP_DIR)
    backup = None
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"profile-{time.strftime('%Y%m%d-%H%M%S')}.json"
        shutil.copyfile(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base, path)
    return backup


def add_phrase(profile: dict, text: str) -> tuple[str, bool]:
    """Add a confirmed new sentence to the phrases. If it is nearly the same as an
    existing phrase, use that one instead. Returns (the phrase, whether it was added)."""
    text = text.strip()
    for phrase in profile["phrases"]:
        if similarity(phrase, text) >= config.SAME_PHRASE_SIMILARITY:
            return phrase, False
    profile["phrases"].append(text)
    return text, True
