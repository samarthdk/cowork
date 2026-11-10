"""Webcam hand-gesture test. No setup needed.

For each visible hand it shows which hand it is (LEFT or RIGHT, from the
user's point of view) and the thumb: UP, DOWN or NONE. Hold a thumb gesture
for about 1 second to select it: a ring fills up around the hand, then
"SELECTED" appears. Each selection is quietly saved as an example so the
detection adapts to the user's hand over time (profiles/gestures.json).

Run from the project folder:
    .venv/bin/python tests/hand_test.py
Keys (click the video window first):
    c = optional calibration (press again to cancel)    q = quit
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
from choose.hands import (CalibrationSession, HoldConfirm, load_model,  # noqa: E402
                          measure, save_model)

MODEL_PATH = config.ROOT / "models" / "hand_landmarker.task"
SELECTED_S = 1.5   # how long "SELECTED" stays on screen
MESSAGE_S = 6      # how long calibration messages stay on screen
RING_RADIUS = 45


def draw_hand(frame, landmarks) -> None:
    h, w = frame.shape[:2]
    for conn in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        p1, p2 = landmarks[conn.start], landmarks[conn.end]
        cv2.line(frame, (int(p1.x * w), int(p1.y * h)), (int(p2.x * w), int(p2.y * h)), (0, 200, 0), 2)
    for p in landmarks:
        cv2.circle(frame, (int(p.x * w), int(p.y * h)), 4, (0, 0, 255), -1)


def draw_ring(frame, landmarks, progress: float, label: str, selected: bool) -> None:
    """Progress ring around the palm: fills as the gesture is held."""
    h, w = frame.shape[:2]
    palm = landmarks[9]
    center = (int(palm.x * w), int(palm.y * h))
    color = (0, 255, 0) if selected else (0, 200, 255)
    cv2.circle(frame, center, RING_RADIUS, (80, 80, 80), 4)
    if progress > 0:
        cv2.ellipse(frame, center, (RING_RADIUS, RING_RADIUS), -90, 0, 360 * progress, color, 8)
        cv2.putText(frame, label, (center[0] - 30, center[1] - RING_RADIUS - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def draw_lines(frame, lines, color=(255, 255, 255), y=40, scale=0.9) -> None:
    for text in lines:
        cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 5)
        cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2)
        y += int(40 * scale)


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

    model = load_model(config.GESTURES_PATH)
    holds = {"LEFT": HoldConfirm(), "RIGHT": HoldConfirm()}
    selected = {}            # side -> (pose, until)
    session = None           # optional calibration, only when c is pressed
    message, message_until = [], 0.0

    start = time.monotonic()
    last_ts = -1
    print("Running. Keys in the video window: c = optional calibration, q = quit.")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the camera feed.")
                break

            frame = cv2.flip(frame, 1)  # mirror view
            aspect = frame.shape[1] / frame.shape[0]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            now = time.monotonic()
            ts = max(int((now - start) * 1000), last_ts + 1)  # must keep increasing
            last_ts = ts

            t0 = time.perf_counter()
            result = landmarker.detect_for_video(image, ts)
            latency_ms = (time.perf_counter() - t0) * 1000

            detections, seen = [], {}
            for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                label = handedness[0].category_name
                hand = measure(landmarks, aspect)
                detections.append((label, hand))
                side = model.side(label)
                if side in holds and side not in seen:
                    seen[side] = (landmarks, hand)
                draw_hand(frame, landmarks)

            if session:
                draw_lines(frame, session.update(now, detections), color=(0, 255, 255))
                if session.done:
                    message = ["Calibration saved."] + model.apply_calibration(session.result)
                    save_model(config.GESTURES_PATH, model)
                    print("\n".join(message))
                    message_until, session = now + MESSAGE_S, None
                    holds = {"LEFT": HoldConfirm(), "RIGHT": HoldConfirm()}
            else:
                lines = []
                for side, hold in holds.items():
                    if side not in seen:
                        hold.update(now, "NONE", None)   # hand gone: counts against
                        continue
                    landmarks, hand = seen[side]
                    answer, reason = model.explain(side, hand)
                    picked, candidate, progress = hold.update(now, answer, hand)
                    if picked:
                        example = hold.example(picked)
                        if example:
                            model.add_example(side, picked, example)
                            save_model(config.GESTURES_PATH, model)
                        selected[side] = (picked, now + SELECTED_S)
                        print(f"SELECTED: {side} thumb {picked}")
                    is_selected = side in selected and now < selected[side][1]
                    ring_label = f"{selected[side][0]} SELECTED" if is_selected else (candidate or "")
                    draw_ring(frame, landmarks, 1.0 if is_selected else progress, ring_label, is_selected)
                    learned = model.limits(side, hand.direction if hand else "UP")[2]
                    lines.append(f"{side} hand - thumb {answer}  ({reason})  learned: {learned}")
                draw_lines(frame, lines or ["No hand raised"])
                if now < message_until:
                    draw_lines(frame, message, color=(0, 255, 0), y=frame.shape[0] - 120, scale=0.7)

            cv2.putText(frame, f"hand model: {latency_ms:.0f} ms   c = calibrate (optional)   q = quit",
                        (20, frame.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imshow("Second Voice - hand test", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                if session:
                    session, message, message_until = None, ["Calibration cancelled."], now + MESSAGE_S
                else:
                    session = CalibrationSession(time.monotonic())
    finally:
        cap.release()
        cv2.destroyAllWindows()
        landmarker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
