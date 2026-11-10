"""Hand gestures for choosing. No setup needed. Project rule: when unsure, select nothing.

Three modes (GestureReader):
  "two"       yes/no by hand: raise your LEFT hand (any pose) = left option,
              RIGHT hand = right option. Hands resting low in the frame are ignored.
  "one_hand"  yes/no with one hand: that hand's thumb UP = yes (left option),
              thumb DOWN = no (right option). The other hand is ignored.
  "four"      four options: LEFT/RIGHT hand x thumb UP/DOWN.

In every mode, if two hands are active at the same moment, nothing is selected.

Thumb rules (GestureModel.explain), all must pass, otherwise NONE:
  1. The four fingers are curled (a flat hand is NONE).
  2. The thumb tip is clearly away from the fingers, not resting on or across
     them (this is what rejects a fist or a tucked thumb).
  3. The thumb sticks out at least half its normal extension. This relaxes
     towards the user's own examples with use, but never below a hard minimum.
  4. The thumb points within 85 degrees of straight up/down, and the tip is
     clearly above (UP) or below (DOWN) the thumb's base joint.

Hold to confirm (HoldConfirm): the same answer must win 80% of frames over 1 s.
Quiet adaptation: each confirmed thumb gesture is saved as an example for that
hand (last 20, in profiles/gestures.json). Calibration (CalibrationSession) is optional.
"""

import json
import math
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path

# ---- Settings ---------------------------------------------------------------

DEFAULT_MIN_REACH = 0.5    # half of a normal thumb extension (normal is about 1.0)
HARD_MIN_REACH = 0.35      # learning can never go below this
MAX_ANGLE = 85             # degrees from straight up/down
MIN_TIP_OFFSET = 0.05      # tip must be this far above/below the base joint (per hand size)
MIN_CLEARANCE = 0.2        # thumb tip's distance from the fingers (per hand size)
MAX_FINGER_CURL = 1.0      # each finger: tip closer to the wrist than its middle knuckle
REST_GAP = 0.1             # with calibration: gestures must reach this much beyond rest
LOW_IN_FRAME = 0.75        # wrist below this (bottom quarter of the picture) = resting

EXAMPLES_PER_HAND = 20     # confirmed gestures remembered per hand
EXAMPLES_FOR_FULL_TRUST = 10

HOLD_S = 1.0               # hold to confirm: look at the last 1 second...
HOLD_SHARE = 0.8           # ...and the answer must win 80% of those frames
RELEASE_SHARE = 0.5        # after selecting, must drop below this before it can fire again

# Before any calibration. MediaPipe's labels came out mirrored on this setup.
DEFAULT_HANDEDNESS = {"Left": "RIGHT", "Right": "LEFT"}
SIDES = ("LEFT", "RIGHT")
POSES = ("UP", "DOWN")
MODES = ("two", "one_hand", "four")
MODE_TITLES = {
    "two": "YES/NO BY HAND: raise LEFT hand = left option, RIGHT hand = right option",
    "one_hand": "YES/NO, ONE HAND ({side}): thumb UP = yes, thumb DOWN = no",
    "four": "FOUR CHOICES: LEFT/RIGHT hand + thumb UP/DOWN",
}

# ---- Measuring a hand -------------------------------------------------------

WRIST, THUMB_MCP, THUMB_TIP, INDEX_MCP, MIDDLE_MCP = 0, 2, 4, 5, 9
FINGERS = [(6, 8), (10, 12), (14, 16), (18, 20)]                # (middle knuckle, tip)
FINGER_CHAINS = [(5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)]


@dataclass
class Hand:
    dir_x: float            # which way the thumb points (unit vector; image y grows down)
    dir_y: float
    reach: float            # thumb tip to index base knuckle, per hand size
    curls: tuple            # per finger; under 1.0 = curled, about 1.3+ = straight
    tip_offset: float = 0.0  # tip height minus base-joint height, per hand size (negative = above)
    clearance: float = 1.0  # thumb tip's distance from the fingers, per hand size
    on_fingers: bool = False  # tip lies over the curled fingers
    wrist_y: float = 0.5    # 0 = top of the picture, 1 = bottom

    @property
    def direction(self) -> str:
        return "UP" if self.tip_offset < 0 else "DOWN"

    @property
    def angle(self) -> float:
        """Degrees away from straight up or straight down (0 = vertical)."""
        if self.dir_x == 0 and self.dir_y == 0:
            return 90.0
        return math.degrees(math.acos(min(1.0, abs(self.dir_y))))

    @property
    def low(self) -> bool:
        return self.wrist_y > LOW_IN_FRAME


def _segment_distance(p, a, b) -> float:
    ax, ay = b[0] - a[0], b[1] - a[1]
    length2 = ax * ax + ay * ay
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / length2))
    return math.hypot(p[0] - (a[0] + t * ax), p[1] - (a[1] + t * ay))


def _convex_hull(points):
    points = sorted(set(points))
    if len(points) < 3:
        return points

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inside(point, hull) -> bool:
    if len(hull) < 3:
        return False
    for i in range(len(hull)):
        a, b = hull[i], hull[(i + 1) % len(hull)]
        if (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) < 0:
            return False
    return True


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

    finger_points = [pt(i) for chain in FINGER_CHAINS for i in chain]
    clearance = min(_segment_distance(tip, pt(a), pt(b))
                    for chain in FINGER_CHAINS for a, b in zip(chain, chain[1:]))
    return Hand(dx, dy, dist(tip, pt(INDEX_MCP)) / hand_size, tuple(curls),
                tip_offset=(tip[1] - base[1]) / hand_size,
                clearance=clearance / hand_size,
                on_fingers=_inside(tip, _convex_hull(finger_points)),
                wrist_y=landmarks[WRIST].y)


def average(hands: list[Hand]) -> Hand:
    n = len(hands)

    def mean(attr):
        return sum(getattr(h, attr) for h in hands) / n

    dx, dy = mean("dir_x"), mean("dir_y")
    norm = math.hypot(dx, dy) or 1.0
    return Hand(dx / norm, dy / norm, mean("reach"),
                tuple(sum(c) / n for c in zip(*(h.curls for h in hands))),
                tip_offset=mean("tip_offset"), clearance=mean("clearance"),
                on_fingers=sum(h.on_fingers for h in hands) > n / 2, wrist_y=mean("wrist_y"))


def _percentile(values: list[float], fraction: float) -> float:
    values = sorted(values)
    return values[round(fraction * (len(values) - 1))]


# ---- Thumb model: rules + this user's examples ------------------------------

class GestureModel:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.handedness = data.get("handedness") or dict(DEFAULT_HANDEDNESS)
        self.hands = {side: {"examples": [], "rest_reach": None} for side in SIDES}
        if data.get("version") == 2:
            for side, info in data.get("hands", {}).items():
                if side in self.hands:
                    self.hands[side]["examples"] = list(info.get("examples", []))[-EXAMPLES_PER_HAND:]
                    self.hands[side]["rest_reach"] = info.get("rest_reach")

    def side(self, mediapipe_label: str) -> str:
        return self.handedness.get(mediapipe_label, "UNKNOWN")

    def min_reach(self, side: str, pose: str) -> tuple[float, int]:
        """(how far the thumb must stick out, examples used) for this hand and gesture."""
        info = self.hands.get(side, {"examples": [], "rest_reach": None})
        examples = [e for e in info["examples"] if e["pose"] == pose]
        reach = DEFAULT_MIN_REACH
        if examples:
            trust = min(1.0, len(examples) / EXAMPLES_FOR_FULL_TRUST)
            personal = _percentile([e["reach"] for e in examples], 0.1) * 0.9
            personal = min(DEFAULT_MIN_REACH, max(HARD_MIN_REACH, personal))  # only relaxes
            reach += trust * (personal - DEFAULT_MIN_REACH)
        if info["rest_reach"] is not None:
            reach = max(reach, info["rest_reach"] + REST_GAP)
        return max(reach, HARD_MIN_REACH), len(examples)

    def classify(self, side: str, hand: Hand | None) -> str:
        return self.explain(side, hand)[0]

    def explain(self, side: str, hand: Hand | None) -> tuple[str, str]:
        """('UP' | 'DOWN' | 'NONE', short reason for the screen)."""
        if hand is None or side not in self.hands:
            return "NONE", "can't read hand"
        if max(hand.curls) >= MAX_FINGER_CURL:
            return "NONE", "fingers not curled"
        if hand.on_fingers or hand.clearance < MIN_CLEARANCE:
            return "NONE", f"thumb resting on fingers ({hand.clearance:.2f})"
        pose = hand.direction
        min_reach, _ = self.min_reach(side, pose)
        if hand.reach < min_reach:
            return "NONE", f"thumb not out far enough ({hand.reach:.2f} < {min_reach:.2f})"
        if hand.angle > MAX_ANGLE or abs(hand.tip_offset) < MIN_TIP_OFFSET:
            return "NONE", f"thumb level, not up or down ({hand.angle:.0f} deg)"
        return pose, f"{hand.angle:.0f} deg, reach {hand.reach:.2f}"

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
            rest = round(_percentile([h.reach for h in poses["REST"]], 0.9), 3)
            self.hands[side] = {"examples": [], "rest_reach": rest}
            for pose in POSES:
                usable = [h for h in poses[pose] if h.direction == pose and h.angle <= MAX_ANGLE
                          and abs(h.tip_offset) >= MIN_TIP_OFFSET
                          and max(h.curls) < MAX_FINGER_CURL and not h.on_fingers
                          and h.clearance >= MIN_CLEARANCE
                          and h.reach >= max(HARD_MIN_REACH, rest + REST_GAP)]
                if len(usable) < 3:
                    warnings.append(f"{side} thumb {pose}: couldn't tell it apart from rest. "
                                    f"It will often show NONE.")
                    continue
                keep = min(len(usable), EXAMPLES_PER_HAND // 2)
                for i in range(keep):
                    self.add_example(side, pose, usable[int(i * len(usable) / keep)], source="calibration")
        return warnings

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
        self.frames: deque = deque()    # (time, answer, Hand or None)
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
        hands = [h for _, a, h in self.frames if a == pose and h is not None]
        return average(hands) if hands else None


# ---- Modes: turning hands into a choice --------------------------------------

@dataclass
class HandView:
    """What to show for one hand this frame."""
    side: str
    answer: str          # per-frame answer ("RAISED", "UP", "DOWN" or "NONE")
    reason: str
    candidate: str | None = None
    progress: float = 0.0
    option: str | None = None   # what the held answer would select, e.g. "YES"


@dataclass
class Selection:
    mode: str
    option: str          # "LEFT"/"RIGHT" (two), "YES"/"NO" (one_hand), "LEFT UP" etc. (four)
    side: str
    pose: str | None = None


def option_for(mode: str, side: str, answer: str) -> str:
    if mode == "two":
        return side
    if mode == "one_hand":
        return "YES" if answer == "UP" else "NO"
    return f"{side} {answer}"


class GestureReader:
    """One call per video frame: hands in, at most one Selection out."""

    def __init__(self, model: GestureModel, mode: str = "two", one_hand: str = "RIGHT"):
        self.model = model
        self.one_hand = one_hand
        self.set_mode(mode)

    def set_mode(self, mode: str, one_hand: str | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.one_hand = one_hand or self.one_hand
        self.holds = {side: HoldConfirm() for side in SIDES}  # start fresh

    def title(self) -> str:
        return MODE_TITLES[self.mode].format(side=self.one_hand)

    def _answer(self, side: str, hand: Hand) -> tuple[str, str]:
        if self.mode == "one_hand" and side != self.one_hand:
            return "NONE", "ignored in one-hand mode"
        if hand is None:
            return "NONE", "can't read hand"
        if hand.low:
            return "NONE", "resting low in frame"
        if self.mode == "two":
            return "RAISED", "raised"
        return self.model.explain(side, hand)

    def update(self, now: float, hands: dict[str, Hand | None]) -> tuple[list[HandView], Selection | None]:
        """hands: side ("LEFT"/"RIGHT") -> measured Hand, only for hands in view."""
        answers = {side: self._answer(side, hand) for side, hand in hands.items()}
        active = [side for side, (answer, _) in answers.items() if answer != "NONE"]
        if len(active) > 1:  # two hands at once: unclear which one is meant
            answers = {side: ("NONE", "both hands active") for side in answers}

        views, selection = [], None
        for side in SIDES:
            hold = self.holds[side]
            if side not in hands:
                hold.update(now, "NONE", None)  # hand out of view counts against
                continue
            answer, reason = answers[side]
            picked, candidate, progress = hold.update(now, answer, hands[side])
            option = option_for(self.mode, side, candidate) if candidate else None
            views.append(HandView(side, answer, reason, candidate, progress, option))
            if picked:
                pose = picked if picked in POSES else None
                selection = Selection(self.mode, option_for(self.mode, side, picked), side, pose)
                example = hold.example(picked) if pose else None
                if example:  # quiet adaptation (thumb modes only)
                    self.model.add_example(side, pose, example)
        return views, selection


# ---- Optional calibration ---------------------------------------------------

PREPARE_S = 3.0
RECORD_S = 2.0
MIN_SAMPLES = 10
MIN_AGREEMENT = 0.8

STEPS = [("CHECK", "LEFT")] + [(side, pose) for side in SIDES for pose in ("UP", "DOWN", "REST")]
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
