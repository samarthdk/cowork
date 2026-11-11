"""Builds a speech profile and an enrollment/test split from one TORGO speaker.

    .venv/bin/python -m eval.torgo_profile M04          (print the setup)
    .venv/bin/python -m eval.torgo_profile M04 --save   (also write the demo profiles)

The profile fits what the speaker actually says in the recordings:
  - phrases:  every sentence the speaker recorded;
  - commands: the short command words the speaker recorded ("up", "down",
              "back"...; TORGO includes them for exactly this kind of software),
              each mapped to an action on the Mac.

The split (no recording is ever in both sets; the two microphones of one
utterance count as ONE recording):
  - a phrase or command recorded more than once: one recording is used for
    ENROLLMENT (what WhisperKit heard -> what was meant, saved as a learned
    example), the others are held out for TESTING. This is done twice
    ("folds"), swapping which recording is enrolled, to get more test clips.
  - a phrase recorded once: test only. The system knows it as text but has
    never heard this person say it.
  - single words that are NOT in the profile: "outside" test clips, to check
    the system does not confidently turn them into something else.
"""

import collections
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from eval.torgo_data import ROOT, load_index, load_transcripts, normalize  # noqa: E402

# Spoken command word -> (command name, description, safety)
COMMAND_WORDS = {
    "up": ("scroll_up", "Scroll the page up. The person says: up.", "safe"),
    "down": ("scroll_down", "Scroll the page down. The person says: down.", "safe"),
    "back": ("go_back", "Go back to the previous page. The person says: back.", "safe"),
    "forward": ("go_forward", "Go forward to the next page. The person says: forward.", "safe"),
    "left": ("press_left", "Press the left arrow key (previous item). The person says: left.", "safe"),
    "right": ("press_right", "Press the right arrow key (next item). The person says: right.", "safe"),
    "select": ("press_return", "Press Return to select the current item. The person says: select.", "safe"),
}
SAY = {"scroll_up": "Scrolling up.", "scroll_down": "Scrolling down.", "go_back": "Going back.",
       "go_forward": "Going forward.", "press_left": "Left.", "press_right": "Right.",
       "press_return": "Selected."}
ANSWER_WORDS = ("yes", "no")
OUTSIDE_SAMPLE = 60


def sentence(text: str) -> str:
    """TORGO prompts have no punctuation: 'i can read' -> 'I can read.'"""
    text = " ".join(text.split())
    words = ["I" if w == "i" else w for w in text.split()]
    out = " ".join(words)
    return out[0].upper() + out[1:] + "."


def build(speaker: str) -> dict:
    utterances = sorted((u for u in load_index() if u["speaker"] == speaker), key=lambda u: u["id"])
    by_text = collections.defaultdict(list)
    for u in utterances:
        by_text[normalize(u["text"])].append(u)

    phrases, commands, target_of = [], [], {}
    for text in sorted(t for t in by_text if len(t.split()) > 1):
        phrases.append(sentence(text))
        target_of[text] = phrases[-1]
    for word, (name, description, safety) in COMMAND_WORDS.items():
        if word in by_text:
            commands.append({"name": name, "description": description, "safety": safety,
                             "said_as": [word], "args": {}, "say": SAY[name]})
            target_of[word] = name
    profile = {"user": f"TORGO speaker {speaker} (research recordings)", "phrases": phrases,
               "vocabulary": [], "commands": commands, "examples": [],
               "answer_words": {"yes": [], "no": []}}

    def fold(pick_enrolled) -> dict:
        enroll, test = [], []
        for text, target in target_of.items():
            recordings = by_text[text]
            if len(recordings) == 1:
                test.append(recordings[0]["id"])
            else:
                chosen = pick_enrolled(recordings)
                enroll.append(chosen["id"])
                test += [u["id"] for u in recordings if u["id"] != chosen["id"]]
        return {"enroll": enroll, "test": test}

    folds = [fold(lambda r: r[0]), fold(lambda r: r[-1])]
    outside = [u["id"] for text, us in sorted(by_text.items())
               if text not in target_of and text not in ANSWER_WORDS and len(text.split()) == 1 for u in us]
    step = max(1, len(outside) // OUTSIDE_SAMPLE)
    answers = {w: [u["id"] for u in by_text.get(w, [])] for w in ANSWER_WORDS}
    return {"speaker": speaker, "profile": profile,
            "truth": {u["id"]: target_of[normalize(u["text"])] for u in utterances if normalize(u["text"]) in target_of},
            "reference": {u["id"]: u["text"] for u in utterances},
            "enrolled_once": sorted({t for t, us in by_text.items() if t in target_of and len(us) > 1}),
            "folds": folds, "outside": outside[::step][:OUTSIDE_SAMPLE], "answers": answers}


def enrolled_profile(setup: dict, fold: int = 0, variant: str = "plain") -> dict:
    """The profile after enrollment: one learned example per enrollment clip
    (what WhisperKit heard -> what was meant)."""
    from repair.store import add_example
    heard = load_transcripts(setup["speaker"], variant)
    profile = copy.deepcopy(setup["profile"])
    for clip in setup["folds"][fold]["enroll"]:
        text = heard.get(clip, {}).get("heard", "")
        if text:   # a clip WhisperKit heard nothing in teaches nothing
            add_example(profile, text, setup["truth"][clip])
    for word, clips in setup["answers"].items():   # this person's own yes and no (first recording of each)
        for clip in clips[:1]:
            text = heard.get(clip, {}).get("heard", "")
            if text:
                profile["answer_words"][word].append(text)
    return profile


def save_demo_profiles(speaker: str) -> list[Path]:
    """Write the base profile and the enrolled profile for the demo."""
    setup = build(speaker)
    base = config.ROOT / "profiles" / f"torgo_{speaker}.json"
    enrolled = config.ROOT / "profiles" / f"torgo_{speaker}_enrolled.json"
    base.write_text(json.dumps(setup["profile"], indent=2, ensure_ascii=False) + "\n")
    enrolled.write_text(json.dumps(enrolled_profile(setup, 0), indent=2, ensure_ascii=False) + "\n")
    (ROOT / f"setup_{speaker}.json").write_text(json.dumps(setup))
    return [base, enrolled]


if __name__ == "__main__":
    who = sys.argv[1] if len(sys.argv) > 1 else "M04"
    setup = build(who)
    p = setup["profile"]
    print(f"Speaker {who}: {len(p['phrases'])} phrases, {len(p['commands'])} commands "
          f"({', '.join(c['name'] for c in p['commands'])})")
    for n, f in enumerate(setup["folds"]):
        print(f"  fold {n}: {len(f['enroll'])} enrollment clips, {len(f['test'])} test clips "
              f"(overlap: {len(set(f['enroll']) & set(f['test']))})")
    print(f"  outside-the-profile test clips: {len(setup['outside'])};  yes/no clips: "
          f"{ {w: len(c) for w, c in setup['answers'].items()} }")
    if "--save" in sys.argv:
        for path in save_demo_profiles(who):
            print("wrote", path)
