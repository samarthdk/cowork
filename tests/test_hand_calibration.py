"""Checks hand calibration and classification without a camera.

Simulates a user whose LEFT thumb only extends partway, and a camera whose
left/right labels are mirrored. Run:
    .venv/bin/python tests/test_hand_calibration.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from choose.hands import STEPS, Calibration, CalibrationSession  # noqa: E402

random.seed(1)
FPS = 30
# MediaPipe label the camera reports for each of the user's real hands (mirrored).
CAMERA_LABEL = {"LEFT": "Right", "RIGHT": "Left"}

# Features: [thumb_dir_x, thumb_dir_y, thumb_reach, finger_curl]
USER = {
    "RIGHT": {"UP": [0.1, -0.99, 1.10, 0.70], "DOWN": [0.1, 0.99, 1.05, 0.70],
              "REST": [-0.8, -0.3, 0.35, 0.68]},
    # Limited left thumb: DOWN only reaches partway and leans sideways.
    "LEFT": {"UP": [-0.2, -0.95, 0.85, 0.72], "DOWN": [0.5, 0.75, 0.55, 0.72],
             "REST": [0.8, -0.3, 0.32, 0.68]},
}


def noisy(features, amount=0.03):
    return [v + random.gauss(0, amount) for v in features]


def run_calibration(skip_frames_in_step=None):
    """Feed a whole calibration session, frame by frame. Returns the session."""
    session, now, guard = CalibrationSession(0.0), 0.0, 0
    while not session.done and guard < 10_000:
        guard += 1
        kind, what = STEPS[session.step]
        side = "LEFT" if kind == "CHECK" else kind
        pose = "REST" if kind == "CHECK" else what
        hidden = skip_frames_in_step == session.step and skip_frames_in_step is not None
        detections = [] if hidden else [(CAMERA_LABEL[side], noisy(USER[side][pose]))]
        before = session.step
        session.update(now, detections)
        if hidden and session.step == before and session.note:
            skip_frames_in_step = None  # hand reappears after one failed attempt
        now += 1 / FPS
    return session


def check(name, got, want):
    status = "PASS" if got == want else "FAIL"
    print(f"{status}  {name}: got {got}, expected {want}")
    return got == want


ok = True
session = run_calibration()
ok &= check("calibration finished", session.done, True)
cal = Calibration(session.result)
ok &= check("camera 'Right' label means user's LEFT", cal.side("Right"), "LEFT")
ok &= check("camera 'Left' label means user's RIGHT", cal.side("Left"), "RIGHT")

for side in ("LEFT", "RIGHT"):
    for pose, want in (("UP", "UP"), ("DOWN", "DOWN"), ("REST", "NONE")):
        hits = [cal.classify(side, noisy(USER[side][pose])) for _ in range(200)]
        ok &= check(f"{side} {pose} (200 noisy frames, all)", set(hits), {want})

halfway = [(a + b) / 2 for a, b in zip(USER["LEFT"]["DOWN"], USER["LEFT"]["REST"])]
ok &= check("LEFT halfway between DOWN and REST", cal.classify("LEFT", halfway), "NONE")
flat_hand = [0.0, -0.99, 1.1, 1.35]  # thumb up but fingers straight
ok &= check("RIGHT open flat hand", cal.classify("RIGHT", flat_hand), "NONE")
ok &= check("unreadable hand", cal.classify("RIGHT", None), "NONE")
ok &= check("unknown hand side", cal.classify("UNKNOWN", USER["RIGHT"]["UP"]), "NONE")

retried = run_calibration(skip_frames_in_step=2)
ok &= check("step with hand out of view is repeated, then finishes", retried.done, True)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
