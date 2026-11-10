"""Hand gestures: which hand, and thumb UP / DOWN / NONE. No setup needed.

Project rule: when unsure, select nothing.

How a frame is judged (GestureModel.classify):
  - The four fingers must be curled (a flat hand is always NONE).
  - The thumb's direction (tip minus base joint) must be close to straight up
    or down: within 50 degrees by default.
  - The thumb must stick out at least half its normal extension by default.
  Anything else, including a thumb tucked sideways across the fingers, is NONE.

How a gesture is selected (HoldConfirm): the same answer must win about 80% of
frames over 1 second. A progress ring shows how close it is.

Quiet adaptation: every confirmed gesture is saved as an example for that hand
(last 20 per hand, in profiles/gestures.json). As examples build up, that
hand's angle and extension limits relax towards how the person actually moves,
but never past the HARD LIMITS below, so a fist can never become a thumbs up.

Optional calibration (CalibrationSession, key c in the hand test) adds starting
examples and learns how far the thumb sticks out at rest.
"""

import json
import math
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path

# ---- Settings ---------------------------------------------------------------

DEFAULT_MAX_ANGLE = 50     # degrees from straight up/down, before any learning
DEFAULT_MIN_REACH = 0.5    # half of a normal thumb extension (normal is about 1.0)

# HARD LIMITS: learning can relax the defaults only this far.
HARD_MAX_ANGLE = 70        # thumb can never be more sideways than this
HARD_MIN_REACH = 0.35      # thumb must always stick out at least this far
MAX_FINGER_CURL = 1.0      # each finger: tip closer to the wrist than its middle knuckle
REST_GAP = 0.1             # with calibration: gestures must reach this much beyond rest

EXAMPLES_PER_HAND = 20     # how many confirmed gestures we remember per hand
EXAMPLES_FOR_FULL_TRUST = 10   # examples of one gesture before they fully count

HOLD_S = 1.0               # hold to confirm: look at the last 1 second...
HOLD_SHARE = 0.8           # ...and the gesture must win 80% of those frames
RELEASE_SHARE = 0.5        # after selecting, must drop below this before it can fire again

# Before any calibration. The user reported MediaPipe's labels come out mirrored
# on this setup, so they are swapped by default.
DEFAULT_HANDEDNESS = {"Left": "RIGHT", "Right": "LEFT"}
POSES = ("UP", "DOWN")

# ---- Measuring a hand -------------------------------------------------------

WRIST, THUMB_MCP, THUMB_TIP, INDEX_MCP, MIDDLE_MCP = 0, 2, 4, 5, 9
FINGERS = [(6, 8), (10, 12), (14, 16), (18, 20)]   # (middle knuckle, tip)


@dataclass
class Hand:
    dir_x: float       # which way the thumb points (unit vector, image y grows downwards)
    dir_y: float
    reach: float       # thumb tip to index base knuckle, divided by hand size
    curls: tuple       # per finger; under 1.0 = curled, about 1.3+ = straight

    @property
    def direction(self) -> str:
        return "UP" if self.dir_y < 0 else "DOWN"

    @property
    def angle(self) -> float:
        """Degrees away from straight up or straight down (0 = perfectly vertical)."""
        if self.dir_x == 0 and self.dir_y == 0:
            return 90.0
        return math.degrees(math.acos(min(1.0, abs(self.dir_y))))


def measure(landmarks, aspect: float) -> Hand | None:
    """Measure one hand from MediaPipe landmarks. `aspect` = image width / height."""
    def pt(i):
        return landmarks[i].x * aspect, landmarks[i].y

    def dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    wrist = pt(WRIST)
    hand_size = dist(wrist, pt(MIDDLE_MCP))
    if hand_size < 1e-6:
        return None
    base, tip = pt(THUMB_MCP), pt(THUMB_TIP)
    length = dist(base, tip)
    dx, dy = ((tip[0] - base[0]) / length, (tip[1] - base[1]) / length) if length > 1e-6 else (0.0, 0.0)
    curls = []
    for knuckle, fingertip in FINGERS:
        knuckle_d = dist(pt(knuckle), wrist)
        curls.append(dist(pt(fingertip), wrist) / knuckle_d if knuckle_d > 1e-6 else 9.9)
    return Hand(dx, dy, dist(tip, pt(INDEX_MCP)) / hand_size, tuple(curls))


def average(hands: list[Hand]) -> Hand:
    n = len(hands)
    dx, dy = sum(h.dir_x for h in hands) / n, sum(h.dir_y for h in hands) / n
    norm = math.hypot(dx, dy) or 1.0
    return Hand(dx / norm, dy / norm, sum(h.reach for h in hands) / n,
                tuple(sum(c) / n for c in zip(*(h.curls for h in hands))))


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    return values[round(fraction * (len(values) - 1))]


# ---- The gesture model: rules + this user's examples ------------------------

class GestureModel:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.handedness = data.get("handedness") or dict(DEFAULT_HANDEDNESS)
        self.hands = {side: {"examples": [], "rest_reach": None} for side in ("LEFT", "RIGHT")}
        if data.get("version") == 2:
            for side, info in data.get("hands", {}).items():
                if side in self.hands:
                    self.hands[side]["examples"] = list(info.get("examples", []))[-EXAMPLES_PER_HAND:]
                    self.hands[side]["rest_reach"] = info.get("rest_reach")

    # -- left/right
    def side(self, mediapipe_label: str) -> str:
        return self.handedness.get(mediapipe_label, "UNKNOWN")

    # -- thresholds
    def limits(self, side: str, pose: str) -> tuple[float, float, int]:
        """(min_reach, max_angle, examples used) for this hand and gesture."""
        info = self.hands.get(side, {"examples": [], "rest_reach": None})
        examples = [e for e in info["examples"] if e["pose"] == pose]
        min_reach, max_angle = DEFAULT_MIN_REACH, DEFAULT_MAX_ANGLE
        if examples:
            trust = min(1.0, len(examples) / EXAMPLES_FOR_FULL_TRUST)
            personal_reach = _percentile([e["reach"] for e in examples], 0.1) * 0.9
            personal_angle = _percentile([e["angle"] for e in examples], 0.9) + 10
            # Only ever relax the defaults, and never past the hard limits.
            personal_reach = min(DEFAULT_MIN_REACH, max(HARD_MIN_REACH, personal_reach))
            personal_angle = max(DEFAULT_MAX_ANGLE, min(HARD_MAX_ANGLE, personal_angle))
            min_reach += trust * (personal_reach - DEFAULT_MIN_REACH)
            max_angle += trust * (personal_angle - DEFAULT_MAX_ANGLE)
        if info["rest_reach"] is not None:
            # Calibrated rest: a gesture must stick out clearly further than resting.
            min_reach = max(min_reach, info["rest_reach"] + REST_GAP)
        return max(min_reach, HARD_MIN_REACH), max_angle, len(examples)

    # -- one frame
    def classify(self, side: str, hand: Hand | None) -> str:
        return self.explain(side, hand)[0]

    def explain(self, side: str, hand: Hand | None) -> tuple[str, str]:
        """('UP' | 'DOWN' | 'NONE', short reason for the screen)."""
        if hand is None or side not in self.hands:
            return "NONE", "can't read hand"
        if max(hand.curls) >= MAX_FINGER_CURL:
            return "NONE", "fingers not curled"
        pose = hand.direction
        min_reach, max_angle, _ = self.limits(side, pose)
        if hand.angle > max_angle:
            return "NONE", f"thumb sideways ({hand.angle:.0f} deg)"
        if hand.reach < min_reach:
            return "NONE", f"thumb not out far enough ({hand.reach:.2f} < {min_reach:.2f})"
        return pose, f"{hand.angle:.0f} deg, reach {hand.reach:.2f}"

    # -- learning
    def add_example(self, side: str, pose: str, hand: Hand, source: str = "use") -> None:
        examples = self.hands[side]["examples"]
        examples.append({"pose": pose, "angle": round(hand.angle, 1), "reach": round(hand.reach, 3),
                         "curl": round(max(hand.curls), 3), "source": source,
                         "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        del examples[:-EXAMPLES_PER_HAND]

    def apply_calibration(self, result: dict) -> list[str]:
        """Use a finished CalibrationSession. Returns warnings for the screen."""
        warnings = []
        self.handedness = result["handedness"]
        for side, poses in result["hands"].items():
            rest = [h.reach for h in poses["REST"]]
            self.hands[side] = {"examples": [], "rest_reach": round(_percentile(rest, 0.9), 3)}
            for pose in POSES:
                usable = [h for h in poses[pose] if h.direction == pose
                          and h.angle <= HARD_MAX_ANGLE and max(h.curls) < MAX_FINGER_CURL
                          and h.reach >= max(HARD_MIN_REACH, self.hands[side]["rest_reach"] + REST_GAP)]
                if len(usable) < 3:
                    warnings.append(f"{side} thumb {pose}: couldn't tell it apart from rest. "
                                    f"It will often show NONE.")
                    continue
                step = len(usable) / min(len(usable), EXAMPLES_PER_HAND // 2)
                for i in range(min(len(usable), EXAMPLES_PER_HAND // 2)):
                    self.add_example(side, pose, usable[int(i * step)], source="calibration")
        return warnings

    # -- saving
    def to_dict(self) -> dict:
        return {"version": 2, "handedness": self.handedness, "hands": self.hands}


def load_model(path) -> GestureModel:
    """Load saved examples; a missing or unreadable file just means 'start fresh'."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return GestureModel()
    if data.get("version") != 2:  # older calibration format: keep only left/right
        return GestureModel({"handedness": data.get("handedness")})
    return GestureModel(data)


def save_model(path, model: GestureModel) -> None:
    Path(path).write_text(json.dumps(model.to_dict(), indent=2))


# ---- Hold to confirm --------------------------------------------------------

class HoldConfirm:
    """Turns noisy per-frame answers into one deliberate selection, per hand."""

    def __init__(self):
        self.frames: deque = deque()   # (time, answer, Hand or None)
        self.locked: str | None = None  # just selected; waiting for release

    def update(self, now: float, answer: str, hand: Hand | None) -> tuple[str | None, str | None, float]:
        """Returns (selected now or None, current candidate or None, progress 0..1)."""
        self.frames.append((now, answer, hand))
        while self.frames and self.frames[0][0] < now - HOLD_S:
            self.frames.popleft()
        votes = Counter(a for _, a, _ in self.frames if a != "NONE")
        if not votes:
            self.locked = None
            return None, None, 0.0
        candidate, count = votes.most_common(1)[0]
        share = count / len(self.frames)

        if self.locked:
            if candidate == self.locked and share >= RELEASE_SHARE:
                return None, candidate, 1.0
            self.locked = None

        span = self.frames[-1][0] - self.frames[0][0]
        progress = min(1.0, share / HOLD_SHARE) * min(1.0, span / (0.9 * HOLD_S))
        if share >= HOLD_SHARE and span >= 0.9 * HOLD_S:
            self.locked = candidate
            return candidate, candidate, 1.0
        return None, candidate, progress

    def example(self, pose: str) -> Hand | None:
        """The average hand over the frames that voted for `pose` (to learn from)."""
        hands = [h for _, a, h in self.frames if a == pose and h is not None]
        return average(hands) if hands else None


# ---- Optional calibration ---------------------------------------------------

PREPARE_S = 3.0
RECORD_S = 2.0
MIN_SAMPLES = 10
MIN_AGREEMENT = 0.8

STEPS = [("CHECK", "LEFT")] + [(side, pose) for side in ("LEFT", "RIGHT")
                               for pose in ("UP", "DOWN", "REST")]
POSE_WORDS = {"UP": "THUMB UP", "DOWN": "THUMB DOWN", "REST": "REST (closed fist)"}


class CalibrationSession:
    """Optional guided setup. Call update() every frame; it returns lines to show.
    When `done`, pass `result` to GestureModel.apply_calibration()."""

    def __init__(self, now: float):
        self.step, self.step_start = 0, now
        self.samples: list = []
        self.handedness: dict | None = None
        self.hands: dict = {}
        self.note = ""
        self.done = False
        self.result: dict | None = None

    def update(self, now: float, detections: list[tuple[str, Hand | None]]) -> list[str]:
        if self.done:
            return []
        kind, what = STEPS[self.step]
        elapsed = now - self.step_start
        header = f"CALIBRATION (optional) step {self.step + 1}/{len(STEPS)}   c = cancel"
        task = "Raise only your LEFT hand" if kind == "CHECK" else f"{kind} hand only: {POSE_WORDS[what]}"
        if elapsed < PREPARE_S:
            return [header, task, f"Get ready... {math.ceil(PREPARE_S - elapsed)}"] + (
                [self.note] if self.note else [])
        problem = self._record(kind, detections)
        if elapsed < PREPARE_S + RECORD_S:
            return [header, task, f"HOLD STILL... {PREPARE_S + RECORD_S - elapsed:.1f}s"] + (
                [problem] if problem else [])
        self._finish_step(kind, what, now)
        return [header, task, "Got it."]

    def _record(self, kind, detections) -> str:
        if len(detections) != 1:
            return "Show exactly one hand" if detections else "Can't see your hand"
        label, hand = detections[0]
        if kind == "CHECK":
            self.samples.append(label)
        elif self.handedness.get(label) != kind:
            return f"That looks like your other hand - use your {kind} hand"
        elif hand is not None:
            self.samples.append(hand)
        return ""

    def _finish_step(self, kind, what, now) -> None:
        samples, self.samples = self.samples, []
        self.step_start, self.note = now, ""
        if len(samples) < MIN_SAMPLES:
            self.note = "Didn't see that clearly - let's try that step again."
            return
        if kind == "CHECK":
            label, count = Counter(samples).most_common(1)[0]
            if count / len(samples) < MIN_AGREEMENT:
                self.note = "Hand side was unclear - let's try that again."
                return
            self.handedness = {label: "LEFT", ("Right" if label == "Left" else "Left"): "RIGHT"}
        else:
            self.hands.setdefault(kind, {})[what] = samples
        self.step += 1
        if self.step == len(STEPS):
            self.done = True
            self.result = {"handedness": self.handedness, "hands": self.hands}
