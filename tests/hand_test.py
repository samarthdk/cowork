"""Webcam hand-signal test. No setup, no modes.

Hold for about 1 second to send a signal (a ring fills around the hand):
  RIGHT / LEFT       raise that hand, any pose, no clear thumb
  RIGHT_UP ... etc.  raise that hand with a clear thumb up or down
Then lower the hand before the next signal. Hands resting low (below the
grey line) are ignored; two hands raised at once sends nothing.

The bottom panel shows the last signal and, side by side, what it would mean
in a yes/no question and in a four-choice question. Thumb signals are quietly
saved as examples so detection adapts over time (profiles/gestures.json).

Run from the project folder:
    .venv/bin/python tests/hand_test.py
Keys (click the video window first):
    c = optional calibration (press again to cancel)   q = quit
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
from choose.hands import (LOW_IN_FRAME, CalibrationSession, HandSignals,  # noqa: E402
                          load_model, measure, save_model)
from choose.meaning import four_choice, yes_no  # noqa: E402

MODEL_PATH = config.ROOT / "models" / "hand_landmarker.task"
MESSAGE_S = 6
RING_RADIUS = 45
FONT = cv2.FONT_HERSHEY_SIMPLEX


def draw_hand(frame, landmarks) -> None:
    h, w = frame.shape[:2]
    for conn in vision.HandLandmarksConnections.HAND_CONNECTIONS:
        p1, p2 = landmarks[conn.start], landmarks[conn.end]
        cv2.line(frame, (int(p1.x * w), int(p1.y * h)), (int(p2.x * w), int(p2.y * h)), (0, 200, 0), 2)
    for p in landmarks:
        cv2.circle(frame, (int(p.x * w), int(p.y * h)), 4, (0, 0, 255), -1)


def draw_ring(frame, landmarks, progress: float, label: str) -> None:
    """Progress ring around the palm: fills as a signal is held."""
    h, w = frame.shape[:2]
    center = (int(landmarks[9].x * w), int(landmarks[9].y * h))
    cv2.circle(frame, center, RING_RADIUS, (80, 80, 80), 4)
    if progress > 0:
        color = (0, 255, 0) if progress >= 1 else (0, 200, 255)
        cv2.ellipse(frame, center, (RING_RADIUS, RING_RADIUS), -90, 0, 360 * progress, color, 8)
    if label:
        cv2.putText(frame, label, (center[0] - 50, center[1] - RING_RADIUS - 12), FONT, 0.8, (0, 0, 0), 5)
        cv2.putText(frame, label, (center[0] - 50, center[1] - RING_RADIUS - 12), FONT, 0.8, (0, 200, 255), 2)


def draw_lines(frame, lines, color=(255, 255, 255), y=40, scale=0.8) -> None:
    for text in lines:
        cv2.putText(frame, text, (20, y), FONT, scale, (0, 0, 0), 5)
        cv2.putText(frame, text, (20, y), FONT, scale, color, 2)
        y += int(40 * scale)


def draw_panel(frame, signal: str | None, fresh: bool) -> None:
    """Bottom panel: raw signal | yes/no meaning | four-choice meaning."""
    h, w = frame.shape[:2]
    top, col = h - 140, w // 3
    columns = [
        ("SIGNAL", signal or "-"),
        ("YES/NO QUESTION", (yes_no(signal) or "ask again") if signal else "-"),
        ("FOUR-CHOICE QUESTION", ((four_choice(signal) or "ask again").replace("_", " ")) if signal else "-"),
    ]
    for i, (title, value) in enumerate(columns):
        x = i * col
        cv2.rectangle(frame, (x + 8, top), (x + col - 8, h - 45), (40, 40, 40), -1)
        cv2.rectangle(frame, (x + 8, top), (x + col - 8, h - 45), (0, 255, 0) if fresh else (90, 90, 90), 2)
        cv2.putText(frame, title, (x + 20, top + 28), FONT, 0.55, (200, 200, 200), 1)
        color = (0, 255, 0) if fresh else (255, 255, 255)
        if value in ("ask again", "-"):
            color = (150, 150, 150)
        cv2.putText(frame, value, (x + 20, top + 72), FONT, 1.0, color, 2)


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
    detector = HandSignals(model)
    last_signal, last_time = None, -99.0
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
                if side in ("LEFT", "RIGHT") and side not in seen:
                    seen[side] = (landmarks, hand)
                draw_hand(frame, landmarks)

            if session:
                draw_lines(frame, session.update(now, detections), color=(0, 255, 255))
                if session.done:
                    message = ["Calibration saved."] + model.apply_calibration(session.result)
                    save_model(config.GESTURES_PATH, model)
                    print("\n".join(message))
                    message_until, session = now + MESSAGE_S, None
                    detector.reset()
            else:
                views, signal = detector.update(now, {side: hand for side, (_, hand) in seen.items()})
                if signal:
                    last_signal, last_time = signal, now
                    print(f"SIGNAL: {signal:11} yes/no: {yes_no(signal) or 'ask again':9} "
                          f"four-choice: {four_choice(signal) or 'ask again'}")
                    if "_" in signal:
                        save_model(config.GESTURES_PATH, model)  # keep the new example
                lines = []
                for v in views:
                    landmarks, hand = seen[v.side]
                    ring_label = "lower hand" if v.waiting else (v.candidate or "")
                    draw_ring(frame, landmarks, 0.0 if v.waiting else v.progress, ring_label)
                    state = "raised" if v.raised else "not counted"
                    thumb = f"thumb {v.thumb}" if v.raised else ""
                    learned = f"  learned: {model.min_reach(v.side, hand.direction)[1]}" if hand else ""
                    lines.append(f"{v.side} hand: {state}  {thumb}  ({v.reason}){learned}")
                draw_lines(frame, lines or ["No hand in view"])
                if now < message_until:
                    draw_lines(frame, message, color=(0, 255, 0), y=frame.shape[0] - 190, scale=0.7)
                low_y = int(LOW_IN_FRAME * frame.shape[0])  # hands below this line are ignored
                cv2.line(frame, (0, low_y), (frame.shape[1], low_y), (120, 120, 120), 1)
                draw_panel(frame, last_signal, fresh=now - last_time < 1.5)

            cv2.putText(frame, f"{latency_ms:.0f} ms   c = calibrate (optional)   q = quit",
                        (20, frame.shape[0] - 15), FONT, 0.6, (0, 255, 255), 2)
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
