"""Test clips for "Play test clip": real recordings of TORGO speaker M04
(dysarthria), fed into the app as if they were said into the microphone.

Credit (shown on screen): the TORGO database of dysarthric speech,
F. Rudzicz, A. K. Namasivayam and T. Wolff, University of Toronto and
Holland-Bloorview Kids Rehab (Language Resources and Evaluation, 2012).

Every clip here was HELD OUT in the M04 evaluation (never used for learning)
and Second Voice reached the right answer with it there. `heard` is what
WhisperKit really heard in that evaluation; with the fake speech recognizer
it is used as the transcript, so the clip button also works with --fake.
"""

import json
from pathlib import Path

import config

TORGO = config.ROOT / "datasets" / "torgo"
CREDIT = ("Test recordings: the TORGO database of dysarthric speech (Rudzicz, Namasivayam & Wolff, "
          "University of Toronto, 2012). Speaker M04, a man with cerebral palsy.")
CLIP_IDS = [   # commands first, then sentences (all from evaluation fold 0, held out)
    "M04_2_0015",   # "up"
    "M04_2_0034",   # "down"
    "M04_2_0138",   # "back"
    "M04_2_0217",   # I feel I can play this weekend
    "M04_2_0172",   # My sister made the flowered curtains
    "M04_2_0264",   # The humidity is overwhelming there
    "M04_2_0098",   # Well he is nearly ninety-three years old
    "M04_2_0174",   # We gathered shells on the beach
    "M04_2_0158",   # I just try to do my best
    "M04_2_0283",   # It was a good win for us
    "M04_2_0007",   # Grandfather likes to be modern in his language
    "M04_2_0077",   # You wished to know all about my grandfather
]


# The demo, in this order (checked with the real WhisperKit and Bonsai): clips 1-3 dictation, clip 4 Conference mode
DEMO = [
    ("M04_2_0264", "Understood directly",
     "WhisperKit hears \u201cBut humanity is overwhelming.\u201d; it is typed at once"),
    ("M04_2_0174", "Needs \u201cDid you mean\u201d",
     "WhisperKit hears \u201cWe can get a joke on the beach.\u201d; one Yes, then it is typed and learned"),
    ("M04_2_0174", "Replay: now instant",
     "the same recording again: recognised from what was learned, typed instantly"),
    ("M04_2_0007", "Conference: new sentence",
     "WhisperKit hears \u201cCould grandpa like to be a model in his language?\u201d; \u201cDid you mean\u201d "
     "Grandfather likes to be modern in his language? Yes: said aloud (checked 3 of 3 runs, 2026-11-15)"),
]


def load_clips() -> list[dict]:
    """[{"id", "recording", "path", "said", "heard", "seconds", "label", "note", "demo"}]:
    the 4 demo clips first (in order), then the other recordings."""
    try:
        index = {u["id"]: u for u in json.loads((TORGO / "utterances.json").read_text())}
        heard = {}
        for line in (TORGO / "transcripts" / "M04.plain.jsonl").read_text().splitlines():
            record = json.loads(line)
            heard[record["id"]] = record.get("heard", "")
    except (OSError, ValueError):
        return []

    def clip(recording, key, label="", note="", demo=False):
        utterance = index.get(recording)
        if not utterance:
            return None
        path = TORGO / "wav" / utterance["file"]
        if not path.exists():
            return None
        return {"id": key, "recording": recording, "path": str(path), "said": utterance["text"],
                "heard": heard.get(recording, ""), "seconds": utterance.get("duration"),
                "label": label, "note": note, "demo": demo}

    clips = [clip(rec, f"demo{n}", label, note, True) for n, (rec, label, note) in enumerate(DEMO, 1)]
    clips += [clip(rec, rec) for rec in CLIP_IDS]
    return [c for c in clips if c is not None]
