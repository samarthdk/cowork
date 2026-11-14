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


def load_clips() -> list[dict]:
    """[{"id", "path", "said", "heard", "seconds"}] for the clips that are on this Mac."""
    try:
        index = {u["id"]: u for u in json.loads((TORGO / "utterances.json").read_text())}
        heard = {}
        for line in (TORGO / "transcripts" / "M04.plain.jsonl").read_text().splitlines():
            record = json.loads(line)
            heard[record["id"]] = record.get("heard", "")
    except (OSError, ValueError):
        return []
    clips = []
    for clip_id in CLIP_IDS:
        utterance = index.get(clip_id)
        if not utterance:
            continue
        path = TORGO / "wav" / utterance["file"]
        if path.exists():
            clips.append({"id": clip_id, "path": str(path), "said": utterance["text"],
                          "heard": heard.get(clip_id, ""), "seconds": utterance.get("duration")})
    return clips
