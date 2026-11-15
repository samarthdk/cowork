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
    _upgrade(profile, json.loads(base.read_text()))
    return profile


def _upgrade(profile: dict, base: dict) -> None:
    """Fill in fields added after this working profile was made."""
    profile.setdefault("examples", [])
    profile.setdefault("vocabulary", list(base.get("vocabulary", [])))
    base_safety = {c["name"]: c.get("safety") for c in base.get("commands", [])}
    for command in profile["commands"]:
        if "safety" not in command and base_safety.get(command["name"]):
            command["safety"] = base_safety[command["name"]]
    # Commands added to the base later (e.g. open_latest_email) join the working copy, and the
    # base's wording of a command (description, how people say it) is kept up to date.
    # What was learned (examples, phrases, vocabulary) is never touched.
    have = {c["name"]: c for c in profile["commands"]}
    for command in base.get("commands", []):
        mine = have.get(command["name"])
        if mine is None:
            profile["commands"].append(json.loads(json.dumps(command)))
        else:
            for key in ("description", "said_as"):
                if key in command:
                    mine[key] = command[key]


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


# ---- Learned examples: heard text -> what the person meant -----------------

MAX_EXAMPLES = 500  # plenty; the oldest are dropped beyond this


def add_example(profile: dict, heard: str, meant: str, args: dict | None = None,
                new_phrase: bool = False, new_words: list | None = None) -> dict:
    """Save a confirmed result. `new_phrase` / `new_words` record that this
    confirmation also added `meant` to the phrases and words to the vocabulary
    (so undo can remove them again)."""
    example = {"heard": heard.strip(), "meant": meant,
               "args": {k: v for k, v in (args or {}).items() if v},
               "new_phrase": new_phrase, "new_words": list(new_words or []),
               "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    profile.setdefault("examples", []).append(example)
    del profile["examples"][:-MAX_EXAMPLES]
    return example


def undo_last(profile: dict) -> tuple[dict | None, bool]:
    """Remove the last saved example. If that confirmation added a new phrase and
    no other example uses it, remove the phrase too; words it added to the
    vocabulary are removed as well.
    Returns (the removed example or None, whether a phrase was removed)."""
    examples = profile.get("examples", [])
    if not examples:
        return None, False
    removed = examples.pop()
    vocabulary = profile.get("vocabulary", [])
    for word in removed.get("new_words", []):
        if word in vocabulary:
            vocabulary.remove(word)
    phrase_removed = False
    if removed["new_phrase"] and not any(e["meant"] == removed["meant"] for e in examples):
        if removed["meant"] in profile["phrases"]:
            profile["phrases"].remove(removed["meant"])
            phrase_removed = True
    return removed, phrase_removed


def delete_example(profile: dict, example: dict) -> bool:
    """Delete one specific learned example (e.g. one that gave a wrong fast-path
    answer). Phrases and vocabulary are kept: they were confirmed as real."""
    examples = profile.get("examples", [])
    if example in examples:
        examples.remove(example)
        return True
    return False


# ---- Personal vocabulary: names, drinks, foods, places --------------------

MAX_VOCABULARY = 200
COMMON_WORDS = set("""a an the i im i'm me my you your he she it we they them us our their this that
these those is am are was were be been being do does did have has had can could will would shall
should may might must to of in on at by for with from up down out off over into about as and or but
not no yes so if then than too very just please some any all more most much many few one two here
there what which who whom when where why how get got go going want need like let make take give
help hi hello ok okay thanks thank sorry""".split())


def add_vocabulary(profile: dict, words: list, sentence: str) -> list:
    """Add important words from a confirmed sentence. Only words that really
    appear in the sentence count, and common words are skipped.
    Returns the words actually added."""
    vocabulary = profile.setdefault("vocabulary", [])
    known = {w.lower() for w in vocabulary}
    in_sentence = normalize(sentence)
    added = []
    for word in words:
        if not isinstance(word, str):
            continue
        word = " ".join(word.split()).strip(" .,!?'\"")
        key = word.lower()
        if (not word or len(word) > 30 or len(word.split()) > 3 or key in known
                or key in COMMON_WORDS or f" {normalize(word)} " not in f" {in_sentence} "):
            continue
        vocabulary.append(word)
        known.add(key)
        added.append(word)
    del vocabulary[:-MAX_VOCABULARY]
    return added
