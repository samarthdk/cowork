"""Hand gestures, calibrated to each user: which hand, and thumb UP / DOWN / NONE.

Project rule: when unsure, select nothing. The classifier only answers UP or
DOWN when the hand is clearly closer to the user's own UP (or DOWN) example
than to their REST example, AND looks like what they showed in calibration.
Everything else is NONE.

Calibration (about 40 seconds):
  1. "Raise your LEFT hand": learns how the camera's left/right maps to the user's.
  2. For each hand: hold THUMB UP, THUMB DOWN, REST (closed fist), 2 s each.
Saved to profiles/gestures.json.
"""

import json
import math
import time
from collections import Counter
from pathlib import Path

# ---- Measuring a hand -----------------------------------------------------

WRIST, THUMB_MCP, THUMB_TIP, INDEX_MCP, MIDDLE_MCP = 0, 2, 4, 5, 9
FINGERS = [(6, 8), (10, 12), (14, 16), (18, 20)]   # (middle knuckle, tip)
FEATURE_NAMES = ["thumb_dir_x", "thumb_dir_y", "thumb_reach", "finger_curl"]


def measure(landmarks, aspect: float) -> list[float] | None:
    """Four numbers describing the hand, or None if the hand is unreadable.

    thumb_dir_x/y: which way the thumb points (y = -1 straight up, +1 straight down)
    thumb_reach:   how far the thumb tip is from the index base knuckle, per hand size
    finger_curl:   about 0.7 for curled fingers, 1.3 or more for straight ones

    `aspect` is image width / height (MediaPipe's x and y use different units).
    """
    def pt(i):
        return landmarks[i].x * aspect, landmarks[i].y

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    wrist = pt(WRIST)
    hand_size = dist(wrist, pt(MIDDLE_MCP))
    if hand_size < 1e-6:
        return None
    mcp, tip = pt(THUMB_MCP), pt(THUMB_TIP)
    length = dist(mcp, tip)
    dir_x, dir_y = ((tip[0] - mcp[0]) / length, (tip[1] - mcp[1]) / length) if length > 1e-6 else (0.0, 0.0)
    reach = dist(tip, pt(INDEX_MCP)) / hand_size
    curls = []
    for knuckle, fingertip in FINGERS:
        knuckle_d = dist(pt(knuckle), wrist)
        curls.append(dist(pt(fingertip), wrist) / knuckle_d if knuckle_d > 1e-6 else 1.0)
    return [dir_x, dir_y, reach, sum(curls) / len(curls)]


def _distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---- Classifying against the user's own examples ---------------------------

MARGIN = 0.6         # UP must be closer than 0.6 x the distance to REST and to DOWN
OUTLIER_SPREADS = 3  # further than 3 x the calibration spread = "not seen before"
MIN_SPREAD = 0.08    # floor, so a very steady calibration isn't impossibly strict

# Used only before the first calibration. The user reported MediaPipe's labels
# come out mirrored on this setup, so they are swapped by default.
DEFAULT_HANDEDNESS = {"Left": "RIGHT", "Right": "LEFT"}


class Calibration:
    def __init__(self, data: dict):
        self.data = data
        self.handedness = data["handedness"]
        self.hands = data["hands"]

    def side(self, mediapipe_label: str) -> str:
        """The user's real hand ("LEFT"/"RIGHT") for a MediaPipe label."""
        return self.handedness.get(mediapipe_label, "UNKNOWN")

    def classify(self, side: str, features: list[float] | None) -> str:
        """'UP', 'DOWN' or 'NONE'."""
        return self.explain(side, features)[0]

    def explain(self, side: str, features: list[float] | None) -> tuple[str, str]:
        """The answer plus a short reason (shown on screen while testing)."""
        hand = self.hands.get(side)
        if hand is None or features is None:
            return "NONE", "no calibration for this hand"
        d = {pose: _distance(features, hand[pose]["mean"]) for pose in ("UP", "DOWN", "REST")}
        best = min(d, key=d.get)
        if best == "REST":
            return "NONE", "closest to rest"
        other = "DOWN" if best == "UP" else "UP"
        if d[best] > MARGIN * d["REST"] or d[best] > MARGIN * d[other]:
            return "NONE", f"not clearly {best}"
        if d[best] > OUTLIER_SPREADS * max(hand[best]["spread"], MIN_SPREAD):
            return "NONE", "unlike calibration"
        return best, f"matches your {best}"


def load_calibration(path) -> Calibration | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return Calibration(json.loads(path.read_text()))
    except (ValueError, KeyError):
        return None


def save_calibration(path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2))


# ---- Calibration session ---------------------------------------------------

PREPARE_S = 3.0      # time to get into position
HOLD_S = 2.0         # time we record
MIN_SAMPLES = 10     # frames needed for a step to count
MIN_AGREEMENT = 0.8  # handedness check: share of frames that must agree

STEPS = [("CHECK", "LEFT")] + [(side, pose) for side in ("LEFT", "RIGHT")
                               for pose in ("UP", "DOWN", "REST")]
POSE_WORDS = {"UP": "THUMB UP", "DOWN": "THUMB DOWN", "REST": "REST (closed fist)"}


class CalibrationSession:
    """Walks the user through calibration. Call update() once per video frame
    with the detected hands; it returns the instruction lines to show."""

    def __init__(self, now: float):
        self.step = 0
        self.step_start = now
        self.samples: list = []
        self.handedness: dict | None = None
        self.hands: dict = {}
        self.note = ""
        self.done = False
        self.result: dict | None = None
        self.warnings: list[str] = []

    def update(self, now: float, detections: list[tuple[str, list[float] | None]]) -> list[str]:
        """detections: one (mediapipe_label, features) pair per visible hand."""
        if self.done:
            return []
        kind, what = STEPS[self.step]
        elapsed = now - self.step_start
        header = f"CALIBRATION step {self.step + 1}/{len(STEPS)}"
        if kind == "CHECK":
            task = "Raise only your LEFT hand"
        else:
            task = f"{kind} hand only: {POSE_WORDS[what]}"

        if elapsed < PREPARE_S:
            lines = [header, task, f"Get ready... {math.ceil(PREPARE_S - elapsed)}"]
            return lines + ([self.note] if self.note else [])

        # Recording.
        problem = self._record(kind, what, detections)
        if elapsed < PREPARE_S + HOLD_S:
            return [header, task, f"HOLD STILL... {PREPARE_S + HOLD_S - elapsed:.1f}s"] + (
                [problem] if problem else [])

        self._finish_step(kind, what, now)
        return [header, task, "Got it."]

    def _record(self, kind, what, detections) -> str:
        if len(detections) != 1:
            return "Show exactly one hand" if detections else "Can't see your hand"
        label, features = detections[0]
        if kind == "CHECK":
            self.samples.append(label)
            return ""
        if self.handedness.get(label) != kind:
            return f"That looks like your other hand - use your {kind} hand"
        if features is not None:
            self.samples.append(features)
        return ""

    def _finish_step(self, kind, what, now) -> None:
        samples, self.samples = self.samples, []
        self.step_start = now
        self.note = ""
        if len(samples) < MIN_SAMPLES:
            self.note = "Didn't see that clearly - let's try that step again."
            return
        if kind == "CHECK":
            label, count = Counter(samples).most_common(1)[0]
            if count / len(samples) < MIN_AGREEMENT:
                self.note = "Hand side was unclear - let's try that again."
                return
            other = "Right" if label == "Left" else "Left"
            self.handedness = {label: "LEFT", other: "RIGHT"}
        else:
            mean = [sum(col) / len(col) for col in zip(*samples)]
            distances = sorted(_distance(s, mean) for s in samples)
            spread = distances[int(0.9 * (len(distances) - 1))]
            self.hands.setdefault(kind, {})[what] = {
                "mean": [round(v, 4) for v in mean], "spread": round(spread, 4),
                "samples": len(samples)}
        self.step += 1
        if self.step == len(STEPS):
            self._complete()

    def _complete(self) -> None:
        self.done = True
        for side, poses in self.hands.items():
            for pose in ("UP", "DOWN"):
                gap = _distance(poses[pose]["mean"], poses["REST"]["mean"])
                if gap < 2 * OUTLIER_SPREADS * MIN_SPREAD:
                    self.warnings.append(f"{side} thumb {pose} looks a lot like REST; "
                                         f"it may often show NONE. Press c to redo.")
        self.result = {
            "version": 1,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "features": FEATURE_NAMES,
            "handedness": self.handedness,
            "hands": self.hands,
        }
