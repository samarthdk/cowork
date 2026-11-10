"""Hand signals for choosing. No setup needed. Project rule: when unsure, select nothing.

The detector (HandSignals) reports one signal after hold-to-confirm:
  RIGHT / LEFT                      a raised hand in any pose, no clear thumb
  RIGHT_UP, RIGHT_DOWN, LEFT_UP, LEFT_DOWN   a raised hand with a clear thumb
What a signal MEANS (yes, no, a grid square) is decided separately, in
choose/meaning.py, by the question being asked.

Rules:
  - Hands resting low in the frame (wrist in the bottom quarter) are ignored.
  - Two hands raised at the same moment: nothing.
  - Hold to confirm: over the last 1 second, a thumb signal needs the thumb in
    80% of frames. A plain RIGHT/LEFT needs the hand raised in 80% of frames
    AND the thumb in at most 20%. In between (thumb half there) = wait / nothing,
    because plain LEFT and LEFT_UP can mean opposite things.
  - After a signal, the hand must be lowered before it can signal again.

Thumb rules (GestureModel.explain), all must pass, otherwise no thumb:
  1. The four fingers are curled (a flat hand has no thumb signal).
  2. The thumb tip is clearly away from the fingers, not resting on or across
     them (rejects a fist or a tucked thumb).
  3. The thumb sticks out at least half its normal extension. This relaxes
     towards the user's own examples with use, but never below a hard minimum.
  4. The thumb points within 85 degrees of straight up/down, and the tip is
     clearly above (UP) or below (DOWN) the thumb's base joint.

Quiet adaptation: each confirmed thumb signal is saved as an example for that
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
PLAIN_MAX_THUMB = 0.2      # plain RIGHT/LEFT only if the thumb showed in at most 20% of frames
RELEASE_SHARE = 0.5        # after a signal, the hand must be raised in fewer frames than this

# Before any calibration. MediaPipe's labels came out mirrored on this setup.
DEFAULT_HANDEDNESS = {"Left": "RIGHT", "Right": "LEFT"}
SIDES = ("LEFT", "RIGHT")
POSES = ("UP", "DOWN")

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


# ---- Hold to confirm: frames -> one signal ---------------------------------

SIGNALS = ("RIGHT", "LEFT", "RIGHT_UP", "RIGHT_DOWN", "LEFT_UP", "LEFT_DOWN")


@dataclass
class HandView:
    """What to show for one hand this frame."""
    side: str
    raised: bool
    thumb: str               # "UP", "DOWN" or "NONE" this frame
    reason: str
    candidate: str | None    # the signal being held, e.g. "LEFT_UP"
    progress: float          # 0..1 towards confirming it
    waiting: bool = False    # True while the hand must be lowered after a signal


class SignalHold:
    """One hand's last second of frames, turned into at most one signal."""

    def __init__(self, side: str):
        self.side = side
        self.frames: deque = deque()   # (time, raised, thumb, Hand or None)
        self.locked = False

    def update(self, now, raised, thumb, hand) -> tuple[str | None, str | None, float]:
        """Returns (confirmed signal or None, candidate or None, progress 0..1)."""
        self.frames.append((now, raised, thumb if raised else "NONE", hand))
        while self.frames and self.frames[0][0] < now - HOLD_S:
            self.frames.popleft()
        n = len(self.frames)
        raised_share = sum(f[1] for f in self.frames) / n
        thumb_shares = {pose: sum(f[2] == pose for f in self.frames) / n for pose in POSES}
        best_thumb = max(thumb_shares, key=thumb_shares.get)
        thumb_share = thumb_shares[best_thumb]

        if self.locked:
            if raised_share >= RELEASE_SHARE:
                return None, None, 0.0
            self.locked = False

        span = self.frames[-1][0] - self.frames[0][0]
        time_factor = min(1.0, span / (0.9 * HOLD_S))
        if thumb_share > PLAIN_MAX_THUMB:
            candidate, share = f"{self.side}_{best_thumb}", thumb_share
        elif raised_share > 0:
            candidate, share = self.side, raised_share
        else:
            return None, None, 0.0
        progress = min(1.0, share / HOLD_SHARE) * time_factor

        if time_factor >= 1.0 and share >= HOLD_SHARE:
            self.locked = True
            return candidate, candidate, 1.0
        return None, candidate, progress

    def example(self, pose: str) -> Hand | None:
        """The average hand over the frames that showed this thumb (to learn from)."""
        hands = [f[3] for f in self.frames if f[2] == pose and f[3] is not None]
        return average(hands) if hands else None


class HandSignals:
    """The detector. One call per video frame: hands in, at most one signal out."""

    def __init__(self, model: GestureModel):
        self.model = model
        self.reset()

    def reset(self) -> None:
        self.holds = {side: SignalHold(side) for side in SIDES}

    def update(self, now: float, hands: dict) -> tuple[list[HandView], str | None]:
        """hands: side ("LEFT"/"RIGHT") -> measured Hand, only for hands in view."""
        state = {}
        for side, hand in hands.items():
            if hand is None:
                state[side] = (False, "NONE", "can't read hand")
            elif hand.low:
                state[side] = (False, "NONE", "resting low in frame")
            else:
                thumb, reason = self.model.explain(side, hand)
                state[side] = (True, thumb, reason)
        if sum(raised for raised, _, _ in state.values()) > 1:
            state = {side: (False, "NONE", "both hands raised") for side in state}

        views, signal = [], None
        for side in SIDES:
            hold = self.holds[side]
            if side not in hands:
                hold.update(now, False, "NONE", None)  # out of view counts against
                continue
            raised, thumb, reason = state[side]
            confirmed, candidate, progress = hold.update(now, raised, thumb, hands[side])
            views.append(HandView(side, raised, thumb, reason, candidate, progress,
                                  waiting=hold.locked and not confirmed))
            if confirmed:
                signal = confirmed
                if "_" in confirmed:  # thumb signal: quietly learn from it
                    pose = confirmed.split("_")[1]
                    example = hold.example(pose)
                    if example:
                        self.model.add_example(side, pose, example)
        return views, signal


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
