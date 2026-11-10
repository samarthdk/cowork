"""Webcam hand-tracking test, calibrated to the user.

Shows the webcam in a window and prints, for each visible hand:
  - which hand it is (LEFT or RIGHT, from the user's point of view)
  - the thumb: UP, DOWN or NONE (NONE whenever it is not clear)

The first run starts calibration automatically (about 40 seconds) and saves it
to profiles/gestures.json.

Run from the project folder:
    .venv/bin/python tests/hand_test.py
Keys (click the video window first):  c = calibrate again   q = quit
"""

import sys
import time
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from choose.hands import (DEFAULT_HANDEDNESS, CalibrationSession,  # noqa: E402
                          load_calibration, measure, save_calibration)

MODEL_PATH = config.ROOT / "models" / "hand_landmarker.task"
MESSAGE_S = 6  # how long "Calibration saved" (and any warnings) stay on screen


def draw_hand(frame, landmarks) -> None:
    h, w = frame.shape[:2]
    for conn in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        p1, p2 = landmarks[conn.start], landmarks[conn.end]
        cv2.line(frame, (int(p1.x * w), int(p1.y * h)), (int(p2.x * w), int(p2.y * h)), (0, 200, 0), 2)
    for p in landmarks:
        cv2.circle(frame, (int(p.x * w), int(p.y * h)), 4, (0, 0, 255), -1)


def draw_lines(frame, lines, color=(255, 255, 255), y=40, scale=1.0) -> None:
    for text in lines:
        cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5)
        cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)
        y += int(42 * scale)


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

    calibration = load_calibration(config.GESTURES_PATH)
    session = None if calibration else CalibrationSession(time.monotonic())
    message, message_until = [], 0.0

    start = time.monotonic()
    last_ts = -1
    print("Running. Keys in the video window: c = calibrate again, q = quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break

            frame = cv2.flip(frame, 1)  # mirror view, like looking in a mirror
            aspect = frame.shape[1] / frame.shape[0]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            now = time.monotonic()
            ts = max(int((now - start) * 1000), last_ts + 1)  # must keep increasing
            last_ts = ts

            t0 = time.perf_counter()
            result = landmarker.detect_for_video(image, ts)
            latency_ms = (time.perf_counter() - t0) * 1000

            detections = []
            for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                detections.append((handedness[0].category_name, measure(landmarks, aspect)))
                draw_hand(frame, landmarks)

            if session:
                draw_lines(frame, session.update(now, detections), color=(0, 255, 255))
                if session.done:
                    save_calibration(config.GESTURES_PATH, session.result)
                    calibration = load_calibration(config.GESTURES_PATH)
                    message = ["Calibration saved."] + session.warnings
                    for line in message:
                        print(line)
                    message_until = now + MESSAGE_S
                    session = None
            else:
                lines = []
                for label, features in detections:
                    if calibration:
                        side = calibration.side(label)
                        thumb, reason = calibration.explain(side, features)
                    else:
                        side, thumb, reason = DEFAULT_HANDEDNESS.get(label, "UNKNOWN"), "NONE", "not calibrated"
                    lines.append(f"{side} hand raised - thumb {thumb}  ({reason})")
                draw_lines(frame, lines or ["No hand raised"])
                if now < message_until:
                    draw_lines(frame, message, color=(0, 255, 0), y=frame.shape[0] - 120, scale=0.7)

            cv2.putText(frame, f"hand model: {latency_ms:.0f} ms   c = calibrate   q = quit",
                        (20, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("Second Voice - hand test", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                session = CalibrationSession(time.monotonic())
    finally:
        cap.release()
        cv2.destroyAllWindows()
        landmarker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
