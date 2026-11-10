"""Webcam hand-tracking test.

Shows the webcam in a window and prints, for each visible hand:
  - which hand it is (LEFT or RIGHT, from the user's point of view)
  - whether the thumb is UP, DOWN or NONE (NONE whenever it is not clear)

Run from the project folder:
    .venv/bin/python tests/hand_test.py
Press q (with the video window selected) to quit.
"""

import math
import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

# Landmark numbers MediaPipe uses for the hand points we need.
WRIST = 0
THUMB_MCP, THUMB_IP, THUMB_TIP = 2, 3, 4
INDEX_MCP, MIDDLE_MCP = 5, 9
# (middle knuckle, fingertip) for index, middle, ring and little finger.
FINGERS = [(6, 8), (10, 12), (14, 16), (18, 20)]

# Thresholds. All distances are measured relative to hand size
# (wrist to middle-finger base knuckle). Raise a number to make it stricter.
THUMB_MIN_REACH = 0.75     # thumb tip this far from the index base knuckle = extended
THUMB_MIN_STRAIGHT = 0.90  # 1.0 = perfectly straight thumb
THUMB_MAX_TILT_DEG = 35    # how far from straight up/down the thumb may lean


def thumb_state(landmarks, aspect: float = 1.0) -> str:
    """Return 'UP', 'DOWN' or 'NONE' for one hand.

    Project rule: when unsure, select nothing. UP/DOWN needs ALL of:
      1. the thumb is extended (tip far from the index finger's base knuckle),
      2. the thumb is straight, not bent,
      3. it points clearly up or down, not sideways,
      4. the other four fingers are curled (rules out an open flat hand).
    Anything else, including a fist, is NONE.

    `aspect` is image width / height. MediaPipe's x and y are fractions of
    the width and the height, so x is scaled to make distances true.
    """
    def pt(i):
        return landmarks[i].x * aspect, landmarks[i].y

    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    wrist = pt(WRIST)
    hand_size = dist(wrist, pt(MIDDLE_MCP))
    if hand_size < 1e-6:
        return "NONE"
    mcp, ip, tip = pt(THUMB_MCP), pt(THUMB_IP), pt(THUMB_TIP)

    # 1. Extended: in a fist the thumb tip sits close to the index knuckle.
    if dist(tip, pt(INDEX_MCP)) / hand_size < THUMB_MIN_REACH:
        return "NONE"

    # 2. Straight: the straight-line length almost equals the length along the joints.
    along_joints = dist(mcp, ip) + dist(ip, tip)
    if along_joints < 1e-6 or dist(mcp, tip) / along_joints < THUMB_MIN_STRAIGHT:
        return "NONE"

    # 3. Clearly vertical.
    dx, dy = tip[0] - mcp[0], tip[1] - mcp[1]
    length = (dx * dx + dy * dy) ** 0.5
    max_sideways = length * math.sin(math.radians(THUMB_MAX_TILT_DEG))
    if abs(dx) > max_sideways:
        return "NONE"

    # 4. Other fingers curled: each fingertip is closer to the wrist than its middle knuckle.
    for knuckle, fingertip in FINGERS:
        if dist(pt(fingertip), wrist) >= dist(pt(knuckle), wrist):
            return "NONE"

    # Image y grows downwards, so a tip above the base (smaller y) means UP.
    return "UP" if dy < 0 else "DOWN"


def draw_hand(frame, landmarks) -> None:
    h, w = frame.shape[:2]
    for conn in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        p1, p2 = landmarks[conn.start], landmarks[conn.end]
        cv2.line(frame, (int(p1.x * w), int(p1.y * h)), (int(p2.x * w), int(p2.y * h)), (0, 200, 0), 2)
    for p in landmarks:
        cv2.circle(frame, (int(p.x * w), int(p.y * h)), 4, (0, 0, 255), -1)


def main() -> int:
    if not MODEL_PATH.exists():
        print(f"Model file missing: {MODEL_PATH}")
        print("Download it once with:")
        print("  curl -L -o models/hand_landmarker.task "
              "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
              "hand_landmarker/float16/latest/hand_landmarker.task")
        return 1

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
    )
    landmarker = vision.HandLandmarker.create_from_options(options)

    camera = config.pick_camera()  # the MacBook camera, never an iPhone
    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        print("Could not open the webcam. Check System Settings > Privacy & Security > Camera "
              "and allow your Terminal app, then try again.")
        return 1

    start = time.monotonic()
    last_ts = -1
    print("Running. Press q in the video window to quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break

            # Flip so the window acts like a mirror. MediaPipe's left/right labels
            # assume a mirrored (selfie) image, so after this flip its labels
            # match the user's real left and right hands.
            frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = int((time.monotonic() - start) * 1000)
            ts = max(ts, last_ts + 1)  # timestamps must keep increasing
            last_ts = ts

            t0 = time.perf_counter()
            result = landmarker.detect_for_video(image, ts)
            latency_ms = (time.perf_counter() - t0) * 1000

            lines = []
            for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                side = handedness[0].category_name.upper()  # "LEFT" or "RIGHT"
                thumb = thumb_state(landmarks, frame.shape[1] / frame.shape[0])
                lines.append(f"{side} hand raised - thumb {thumb}")
                draw_hand(frame, landmarks)
            if not lines:
                lines.append("No hand raised")

            y = 40
            for text in lines:
                cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 5)
                cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
                y += 45
            cv2.putText(frame, f"hand model: {latency_ms:.0f} ms   (q = quit)",
                        (20, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

            cv2.imshow("Second Voice - hand test", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        landmarker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
