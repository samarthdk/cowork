"""Checks hand gestures without a camera: default rules, hold to confirm,
quiet adaptation (with its hard limits), saving, and optional calibration.
Run:
    .venv/bin/python tests/test_hand_gestures.py
"""

import math
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from choose import hands as H  # noqa: E402

random.seed(1)
FPS = 30
results = []


def hand(pose, angle, reach, curl=0.75):
    """A measured hand: thumb `angle` degrees off straight up/down."""
    a = math.radians(angle)
    return H.Hand(math.sin(a), -math.cos(a) if pose == "UP" else math.cos(a), reach, (curl,) * 4)


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got}, expected {want}")


def hold_for(model, side, make_hand, seconds, hold=None):
    """Feed frames for `seconds`; return (selections, hold)."""
    hold = hold or H.HoldConfirm()
    picks = []
    for i in range(int(seconds * FPS)):
        h = make_hand(i)
        picked, _, _ = hold.update(hold_for.t, model.classify(side, h), h)
        hold_for.t += 1 / FPS
        if picked:
            picks.append(picked)
            model.add_example(side, picked, hold.example(picked))
    return picks, hold


hold_for.t = 0.0

print("-- 1. Default rules, no calibration --")
m = H.GestureModel()
check("thumb straight up", m.classify("LEFT", hand("UP", 0, 1.0)), "UP")
check("thumb straight down", m.classify("LEFT", hand("DOWN", 0, 1.0)), "DOWN")
check("hand tilted, thumb 45 deg off vertical", m.classify("LEFT", hand("UP", 45, 0.9)), "UP")
check("thumb 60 deg off vertical", m.classify("LEFT", hand("UP", 60, 0.9)), "NONE")
check("thumb tucked sideways across fingers", m.classify("LEFT", hand("UP", 85, 0.6)), "NONE")
check("partial thumb, 55% extension", m.classify("LEFT", hand("DOWN", 20, 0.55)), "DOWN")
check("closed fist (thumb barely out)", m.classify("LEFT", hand("UP", 30, 0.25)), "NONE")
check("open flat hand, thumb up", m.classify("LEFT", hand("UP", 10, 1.0, curl=1.35)), "NONE")
check("left/right swapped by default", m.side("Right"), "LEFT")

print("-- 2. Hold to confirm --")
m = H.GestureModel()
picks, hold = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0), 0.7)
check("0.7 s hold: nothing selected yet", picks, [])
picks, hold = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0), 0.5, hold)
check("keep holding to ~1.2 s: selected once", picks, ["UP"])
picks, hold = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0), 2.0, hold)
check("keep holding 2 s more: no repeat", picks, [])
picks, hold = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 0.2), 1.0, hold)
picks, hold = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0), 1.2, hold)
check("relax, then thumbs up again: selected again", picks, ["UP"])
m = H.GestureModel()
picks, _ = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0) if i % 3 else hand("UP", 5, 0.2), 3)
check("flickering 67% of frames: never selected", picks, [])
picks, _ = hold_for(m, "RIGHT", lambda i: hand("UP", 5, 1.0) if i % 2 else hand("DOWN", 5, 1.0), 3)
check("flipping UP/DOWN: never selected", picks, [])

print("-- 3. Quiet adaptation --")
m = H.GestureModel()
before = m.classify("LEFT", hand("DOWN", 55, 0.6))
for _ in range(12):  # user's DOWN is usually a bit sideways (45-50 deg)
    hold_for(m, "LEFT", lambda i: hand("DOWN", random.uniform(42, 49), 0.6), 1.3)
    hold_for(m, "LEFT", lambda i: hand("DOWN", 5, 0.1), 0.5)  # relax between
after = m.classify("LEFT", hand("DOWN", 55, 0.6))
beyond = m.classify("LEFT", hand("DOWN", 65, 0.6))
check("before learning: DOWN at 55 deg", before, "NONE")
check("after 12 uses near 42-49 deg: DOWN at 55 deg", after, "DOWN")
check("after learning: DOWN at 65 deg (beyond your range)", beyond, "NONE")
check("other hand unaffected", m.classify("RIGHT", hand("DOWN", 55, 0.6)), "NONE")
check("one example saved per selection", len(m.hands["LEFT"]["examples"]), 12)

m = H.GestureModel()
for _ in range(200):  # worst case: examples right at the edge, over and over
    m.add_example("LEFT", "UP", hand("UP", H.HARD_MAX_ANGLE, H.HARD_MIN_REACH))
reach, angle, n = m.limits("LEFT", "UP")
check("after 200 edge examples: kept 20", n, 20)
check("limits never pass the hard limits",
      (reach >= H.HARD_MIN_REACH, angle <= H.HARD_MAX_ANGLE), (True, True))
check("fist still NONE after maximum learning", m.classify("LEFT", hand("UP", 30, 0.25)), "NONE")
check("tucked thumb still NONE after maximum learning", m.classify("LEFT", hand("UP", 85, 0.6)), "NONE")
check("flat hand still NONE after maximum learning",
      m.classify("LEFT", hand("UP", 10, 1.0, curl=1.3)), "NONE")

print("-- 4. Saving and loading --")
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "gestures.json"
    H.save_model(path, m)
    check("examples survive save + load", len(H.load_model(path).hands["LEFT"]["examples"]), 20)
    path.write_text('{"version": 1, "handedness": {"Left": "LEFT", "Right": "RIGHT"}, "hands": {}}')
    check("old calibration file: keeps left/right", H.load_model(path).side("Left"), "LEFT")
    path.write_text("not json")
    check("broken file: starts fresh", len(H.load_model(path).hands["LEFT"]["examples"]), 0)
check("no file: starts fresh", H.load_model("/nonexistent/gestures.json").side("Right"), "LEFT")

print("-- 5. Optional calibration (limited LEFT thumb) --")
CAMERA_LABEL = {"LEFT": "Right", "RIGHT": "Left"}
POSE = {"LEFT": {"UP": ("UP", 20, 0.42), "DOWN": ("DOWN", 30, 0.42), "REST": ("UP", 70, 0.2)},
        "RIGHT": {"UP": ("UP", 5, 1.0), "DOWN": ("DOWN", 5, 1.0), "REST": ("UP", 80, 0.3)}}
m = H.GestureModel()
check("before: limited LEFT DOWN (42% reach)", m.classify("LEFT", hand("DOWN", 30, 0.42)), "NONE")
s, t = H.CalibrationSession(0.0), 0.0
while not s.done:
    kind, what = H.STEPS[s.step]
    side, pose = ("LEFT", "REST") if kind == "CHECK" else (kind, what)
    p, a, r = POSE[side][pose]
    s.update(t, [(CAMERA_LABEL[side], hand(p, a + random.gauss(0, 2), r + random.gauss(0, 0.01)))])
    t += 1 / FPS
warnings = m.apply_calibration(s.result)
check("calibration warnings", warnings, [])
check("after: limited LEFT DOWN", m.classify("LEFT", hand("DOWN", 30, 0.42)), "DOWN")
check("after: LEFT resting thumb", m.classify("LEFT", hand("UP", 30, 0.22)), "NONE")
check("after: RIGHT still works", m.classify("RIGHT", hand("UP", 5, 1.0)), "UP")
check("camera 'Right' label = user's LEFT", m.side("Right"), "LEFT")

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
