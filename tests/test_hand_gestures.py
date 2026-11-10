"""Checks hand gestures without a camera: thumb rules, the three modes, hold to
confirm, quiet adaptation (with its hard limits), saving, optional calibration.
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


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got}, expected {want}")


def hand(pose, angle, reach, curl=0.75, clearance=0.5, on_fingers=False, wrist_y=0.5):
    """A measured hand: thumb `angle` degrees off straight up/down, thumb length 0.6."""
    a = math.radians(angle)
    dy = -math.cos(a) if pose == "UP" else math.cos(a)
    return H.Hand(math.sin(a), dy, reach, (curl,) * 4, tip_offset=0.6 * dy,
                  clearance=clearance, on_fingers=on_fingers, wrist_y=wrist_y)


# -- real landmark shapes, measured with measure() (x right, y down; wrist at 0,0)
class P:
    def __init__(s, x, y):
        s.x, s.y = x * 0.2 / (4 / 3) + 0.5, y * 0.2 + 0.5


def shape(thumb, mcps, pips, tips):
    pts = [(0, 0)] + thumb + [None] * 16
    for i, (m, p, t) in enumerate(zip(mcps, pips, tips)):
        b = 5 + 4 * i
        pts[b], pts[b + 1], pts[b + 3] = m, p, t
        pts[b + 2] = ((p[0] + t[0]) / 2, (p[1] + t[1]) / 2)
    return H.measure([P(*q) for q in pts], 4 / 3)


SIDE = ([(0.6, -0.8), (0.6, -0.6), (0.6, -0.4), (0.55, -0.2)],   # fist seen from the side
        [(1.0, -0.8), (1.0, -0.55), (1.0, -0.35), (0.95, -0.15)],
        [(0.7, -0.7), (0.7, -0.5), (0.7, -0.3), (0.7, -0.1)])
FRONT = ([(-0.3, -0.9), (-0.1, -0.95), (0.1, -0.9), (0.3, -0.8)],  # fist seen from the front
         [(-0.3, -1.2), (-0.1, -1.25), (0.1, -1.2), (0.3, -1.05)],
         [(-0.3, -0.75), (-0.1, -0.8), (0.1, -0.75), (0.3, -0.7)])


def thumb_at(angle, length=0.8, pointing="UP", away=-1):
    """Thumb from base (0.4,-1.0) at `angle` deg off vertical, leaning away from the fingers."""
    a = math.radians(angle)
    sy = -1 if pointing == "UP" else 1
    base = (0.4, -1.0) if pointing == "UP" else (0.4, -0.1)
    joint = lambda f: (base[0] + away * math.sin(a) * length * f, base[1] + sy * math.cos(a) * length * f)  # noqa
    return [(0.3, -0.6), base, joint(0.5), joint(1.0)]


m = H.GestureModel()
print("-- 1. Thumb rules on real hand shapes --")
check("thumbs up", m.classify("RIGHT", shape(thumb_at(5), *SIDE)), "UP")
check("thumb out 72 deg sideways, tip above base", m.classify("RIGHT", shape(thumb_at(72), *SIDE)), "UP")
check("thumb out 80 deg sideways", m.classify("RIGHT", shape(thumb_at(80), *SIDE)), "UP")
check("thumb level (90 deg)", m.classify("RIGHT", shape(thumb_at(90), *SIDE)), "NONE")
fist = shape([(-0.35, -0.25), (-0.5, -0.5), (-0.4, -0.75), (-0.2, -0.85)], *FRONT)
check("fist, thumb across the fingers", m.classify("RIGHT", fist), "NONE")
check("  ...because it is resting on the fingers", m.explain("RIGHT", fist)[1].startswith("thumb resting"), True)
tucked = shape([(0.3, -0.6), (0.5, -0.9), (0.8, -0.85), (1.05, -0.75)], *SIDE)
check("thumb tucked over the curled fingers", m.classify("RIGHT", tucked), "NONE")
flat = shape(thumb_at(10), [(-0.3, -0.9), (-0.1, -0.95), (0.1, -0.9), (0.3, -0.8)],
             [(-0.35, -1.4), (-0.1, -1.5), (0.15, -1.4), (0.4, -1.2)],
             [(-0.4, -1.8), (-0.1, -1.95), (0.2, -1.8), (0.5, -1.5)])
check("open flat hand", m.classify("RIGHT", flat), "NONE")

print("-- 2. Thumb rules on measurements --")
check("thumb down", m.classify("LEFT", hand("DOWN", 0, 1.0)), "DOWN")
check("partial thumb, 55% extension", m.classify("LEFT", hand("DOWN", 20, 0.55)), "DOWN")
check("thumb barely out (25%)", m.classify("LEFT", hand("UP", 30, 0.25)), "NONE")
check("thumb close to fingers", m.classify("LEFT", hand("UP", 30, 0.8, clearance=0.1)), "NONE")
check("left/right swapped by default", m.side("Right"), "LEFT")


def run(reader, frames_for, seconds):
    """Feed frames; frames_for(i) -> {side: Hand}. Returns the list of selected options."""
    picks = []
    for i in range(int(seconds * FPS)):
        _, sel = reader.update(run.t, frames_for(i))
        run.t += 1 / FPS
        if sel:
            picks.append(sel.option)
    return picks


run.t = 0.0
UP, DOWN, FIST = hand("UP", 5, 1.0), hand("DOWN", 5, 1.0), hand("UP", 30, 0.2)
OPEN = hand("UP", 10, 1.0, curl=1.4)

print("-- 3. Two-choice mode (yes/no by hand) --")
r = H.GestureReader(H.GestureModel(), "two")
check("raise LEFT hand, open palm, 1.2 s", run(r, lambda i: {"LEFT": OPEN}, 1.2), ["LEFT"])
r.set_mode("two")
check("raise RIGHT hand, fist, 1.2 s", run(r, lambda i: {"RIGHT": FIST}, 1.2), ["RIGHT"])
r.set_mode("two")
check("hand resting low on the table", run(r, lambda i: {"LEFT": hand("UP", 5, 1, wrist_y=0.9)}, 2), [])
r.set_mode("two")
check("both hands raised at once", run(r, lambda i: {"LEFT": OPEN, "RIGHT": OPEN}, 2), [])
r.set_mode("two")
check("right hand raised, left resting low",
      run(r, lambda i: {"RIGHT": OPEN, "LEFT": hand("UP", 5, 1, wrist_y=0.9)}, 1.2), ["RIGHT"])
r.set_mode("two")
check("only 0.6 s", run(r, lambda i: {"LEFT": OPEN}, 0.6), [])
r.set_mode("two")
check("hand in view only 70% of frames", run(r, lambda i: {"LEFT": OPEN} if i % 10 < 7 else {}, 3), [])

print("-- 4. One-hand yes/no --")
r = H.GestureReader(H.GestureModel(), "one_hand", one_hand="LEFT")
check("LEFT thumb up = YES", run(r, lambda i: {"LEFT": UP}, 1.2), ["YES"])
r.set_mode("one_hand")
check("LEFT thumb down = NO", run(r, lambda i: {"LEFT": DOWN}, 1.2), ["NO"])
r.set_mode("one_hand")
check("RIGHT hand ignored", run(r, lambda i: {"RIGHT": UP}, 2), [])
r.set_mode("one_hand")
check("LEFT fist", run(r, lambda i: {"LEFT": FIST}, 2), [])
r.set_mode("one_hand", "RIGHT")
check("switched to RIGHT hand: thumb up = YES", run(r, lambda i: {"RIGHT": UP}, 1.2), ["YES"])

print("-- 5. Four-choice mode --")
r = H.GestureReader(H.GestureModel(), "four")
check("LEFT thumb down", run(r, lambda i: {"LEFT": DOWN}, 1.2), ["LEFT DOWN"])
r.set_mode("four")
check("RIGHT thumb up", run(r, lambda i: {"RIGHT": UP}, 1.2), ["RIGHT UP"])
r.set_mode("four")
check("RIGHT thumb up, LEFT fist raised", run(r, lambda i: {"RIGHT": UP, "LEFT": FIST}, 1.2), ["RIGHT UP"])
r.set_mode("four")
check("both thumbs up at once", run(r, lambda i: {"RIGHT": UP, "LEFT": UP}, 2), [])
r.set_mode("four")
check("open hand only", run(r, lambda i: {"RIGHT": OPEN}, 2), [])

print("-- 6. Hold to confirm --")
r = H.GestureReader(H.GestureModel(), "four")
check("hold 3 s: selects once, no repeats", run(r, lambda i: {"RIGHT": UP}, 3), ["RIGHT UP"])
run(r, lambda i: {"RIGHT": FIST}, 1)
check("relax, then again: selects again", run(r, lambda i: {"RIGHT": UP}, 1.2), ["RIGHT UP"])
r.set_mode("four")
check("flickering 67% of frames", run(r, lambda i: {"RIGHT": UP if i % 3 else FIST}, 3), [])
r.set_mode("four")
check("flipping UP/DOWN", run(r, lambda i: {"RIGHT": UP if i % 2 else DOWN}, 3), [])

print("-- 7. Quiet adaptation --")
model = H.GestureModel()
r = H.GestureReader(model, "one_hand", one_hand="LEFT")
check("before learning: 45% reach", model.classify("LEFT", hand("DOWN", 30, 0.45)), "NONE")
for _ in range(12):  # this user's DOWN only just passes (50-56% reach)
    run(r, lambda i: {"LEFT": hand("DOWN", 30, random.uniform(0.5, 0.56))}, 1.2)
    run(r, lambda i: {"LEFT": FIST}, 0.6)
check("one example saved per selection", len(model.hands["LEFT"]["examples"]), 12)
check("after 12 uses (usual reach ~0.53): 48% reach", model.classify("LEFT", hand("DOWN", 30, 0.48)), "DOWN")
check("after 12 uses: 44% reach is still too far below usual", model.classify("LEFT", hand("DOWN", 30, 0.44)), "NONE")
check("other hand unaffected", model.classify("RIGHT", hand("DOWN", 30, 0.45)), "NONE")
check("two-choice mode saves no thumb examples",
      (run(H.GestureReader(model, "two"), lambda i: {"RIGHT": UP}, 1.2), len(model.hands["RIGHT"]["examples"])),
      (["RIGHT"], 0))
model = H.GestureModel()
for _ in range(200):
    model.add_example("LEFT", "UP", hand("UP", 80, H.HARD_MIN_REACH))
reach, n = model.min_reach("LEFT", "UP")
check("after 200 edge examples: keeps 20, never below hard minimum", (n, reach >= H.HARD_MIN_REACH), (20, True))
check("fist still NONE after maximum learning", model.classify("LEFT", fist), "NONE")
check("tucked thumb still NONE after maximum learning", model.classify("LEFT", tucked), "NONE")

print("-- 8. Saving and loading --")
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "gestures.json"
    H.save_model(path, model)
    check("examples survive save + load", len(H.load_model(path).hands["LEFT"]["examples"]), 20)
    path.write_text('{"version": 1, "handedness": {"Left": "LEFT", "Right": "RIGHT"}, "hands": {}}')
    check("old calibration file: keeps left/right", H.load_model(path).side("Left"), "LEFT")
    path.write_text("not json")
    check("broken file: starts fresh", len(H.load_model(path).hands["LEFT"]["examples"]), 0)
real = Path(__file__).resolve().parent.parent / "profiles" / "gestures.json"
if real.exists():
    check("your saved gestures.json still loads", isinstance(H.load_model(real), H.GestureModel), True)

print("-- 9. Optional calibration (limited LEFT thumb) --")
CAMERA_LABEL = {"LEFT": "Right", "RIGHT": "Left"}
POSE = {"LEFT": {"UP": ("UP", 72, 0.42), "DOWN": ("DOWN", 30, 0.42), "REST": ("UP", 60, 0.2)},
        "RIGHT": {"UP": ("UP", 5, 1.0), "DOWN": ("DOWN", 5, 1.0), "REST": ("UP", 80, 0.3)}}
model = H.GestureModel()
check("before: limited LEFT DOWN (42% reach)", model.classify("LEFT", hand("DOWN", 30, 0.42)), "NONE")
s, t = H.CalibrationSession(0.0), 0.0
while not s.done:
    kind, what = H.STEPS[s.step]
    side, pose = ("LEFT", "REST") if kind == "CHECK" else (kind, what)
    p, a, rr = POSE[side][pose]
    s.update(t, [(CAMERA_LABEL[side], hand(p, a + random.gauss(0, 2), rr + random.gauss(0, 0.01)))])
    t += 1 / FPS
check("calibration warnings", model.apply_calibration(s.result), [])
check("after: limited LEFT DOWN", model.classify("LEFT", hand("DOWN", 30, 0.42)), "DOWN")
check("after: limited LEFT UP at 72 deg", model.classify("LEFT", hand("UP", 72, 0.42)), "UP")
check("after: LEFT resting thumb", model.classify("LEFT", hand("UP", 30, 0.22)), "NONE")
check("after: RIGHT still works", model.classify("RIGHT", hand("UP", 5, 1.0)), "UP")

print(f"\n{sum(results)}/{len(results)} passed" + ("  ALL PASS" if all(results) else "  SOME FAILED"))
sys.exit(0 if all(results) else 1)
