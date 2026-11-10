"""Webcam hand-tracking test.

Shows the webcam in a window and prints, for each visible hand:
  - which hand it is (LEFT or RIGHT, from the user's point of view)
  - whether the thumb is UP or DOWN

Run from the project folder:
    .venv/bin/python tests/hand_test.py
Press q (with the video window selected) to quit.
"""

import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"

# Landmark numbers MediaPipe uses for the hand points we need.
WRIST, THUMB_MCP, THUMB_TIP, MIDDLE_MCP = 0, 2, 4, 9

# How far (as a fraction of hand size) the thumb tip must be above/below
# its base knuckle to count as UP or DOWN. Bigger = stricter.
THUMB_THRESHOLD = 0.35


def thumb_state(landmarks) -> str:
    """Return 'UP', 'DOWN' or 'SIDEWAYS' for one hand's landmarks."""
    tip, base = landmarks[THUMB_TIP], landmarks[THUMB_MCP]
    wrist, middle = landmarks[WRIST], landmarks[MIDDLE_MCP]
    hand_size = ((wrist.x - middle.x) ** 2 + (wrist.y - middle.y) ** 2) ** 0.5 or 1e-6
    # Image y grows downwards, so a positive value means the tip is higher.
    rise = (base.y - tip.y) / hand_size
    if rise > THUMB_THRESHOLD:
        return "UP"
    if rise < -THUMB_THRESHOLD:
        return "DOWN"
    return "SIDEWAYS"


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

    cap = cv2.VideoCapture(0)
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
                thumb = thumb_state(landmarks)
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
