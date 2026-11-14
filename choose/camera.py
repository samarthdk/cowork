"""The camera, feeding hand signals into the app (choose/hands.py does the reading).

    camera = Camera(on_signal=app.signal, on_hold=app.hold)
    camera.want(True)     # a question is open: camera on, preview frames made
    camera.want(False)    # closes the camera a few seconds later
    camera.jpeg()         # the newest preview frame (mirrored, hand lines drawn), or None

The camera is only on while a question is open (plus KEEP_ON_S afterwards, as
questions often come one after another). So the green camera light means
"I am watching for your answer", and nothing is ever recorded or saved.
The same rules as tests/hand_test.py: hold about 1 second; when unsure, nothing.
"""

import threading
import time

import config
from choose.hands import HandSignals, load_model, measure, save_model

MODEL_PATH = config.ROOT / "models" / "hand_landmarker.task"
KEEP_ON_S = 6.0
PREVIEW_WIDTH = 360
FPS = 15


class Camera:
    def __init__(self, on_signal, on_hold=None, on_state=None):
        self.on_signal, self.on_hold = on_signal, on_hold or (lambda s, p: None)
        self.on_state = on_state or (lambda state: None)
        self.problem = ""
        self.running = False      # the camera is open right now
        self._wanted_until = 0.0
        self._wanted = False
        self._frame = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._work, daemon=True, name="camera")
        self._thread.start()

    @staticmethod
    def available() -> str:
        """'' if the camera can be used, otherwise why not."""
        if not MODEL_PATH.exists():
            return "The hand model file is missing (models/hand_landmarker.task)."
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
        except ImportError as error:
            return f"Camera software missing: {error}"
        return ""

    def want(self, on: bool) -> None:
        self._wanted = on
        if not on:
            self._wanted_until = time.monotonic() + KEEP_ON_S
        self._wake.set()

    def jpeg(self) -> bytes | None:
        with self._lock:
            return self._frame

    def _needed(self) -> bool:
        return self._wanted or time.monotonic() < self._wanted_until

    def _work(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            while self._needed():
                try:
                    self._run()
                except Exception as error:   # never crash the app over the camera
                    self.problem = f"Camera stopped: {type(error).__name__}: {error}"
                    self.running = False
                    self.on_state({"on": False, "problem": self.problem})
                    time.sleep(2.0)
                if self.problem:
                    time.sleep(2.0)   # don't retry a broken camera in a tight loop

    def _run(self) -> None:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=vision.RunningMode.VIDEO, num_hands=2))
        capture = cv2.VideoCapture(config.pick_camera())
        if not capture.isOpened():
            landmarker.close()
            self.problem = ("Could not open the camera. System Settings > Privacy & Security > Camera: "
                            "allow Terminal.")
            self.on_state({"on": False, "problem": self.problem})
            time.sleep(3.0)
            return
        model = load_model(config.GESTURES_PATH)
        detector = HandSignals(model)
        self.running, self.problem = True, ""
        self.on_state({"on": True, "problem": ""})
        start, last_ts, failures = time.monotonic(), -1, 0
        try:
            while self._needed():
                tick = time.monotonic()
                ok, frame = capture.read()
                if not ok:   # the first frames often fail while the camera warms up
                    failures += 1
                    if failures > FPS * 3:
                        self.problem = "Lost the camera picture."
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                frame = cv2.flip(frame, 1)   # mirror, like a mirror
                aspect = frame.shape[1] / frame.shape[0]
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                now = time.monotonic()
                ts = max(int((now - start) * 1000), last_ts + 1)
                last_ts = ts
                result = landmarker.detect_for_video(image, ts)
                seen = {}
                for landmarks, handedness in zip(result.hand_landmarks, result.handedness):
                    hand = measure(landmarks, aspect)
                    side = model.side(handedness[0].category_name)
                    if side in ("LEFT", "RIGHT") and side not in seen:
                        seen[side] = hand
                    _draw(frame, landmarks)
                views, signal = detector.update(now, seen)
                if self._wanted:
                    best = max((v for v in views if not v.waiting and v.candidate), key=lambda v: v.progress,
                               default=None)
                    self.on_hold(best.candidate if best else None, best.progress if best else 0.0)
                if signal:
                    used = self.on_signal(signal)
                    if used and "_" in signal:
                        save_model(config.GESTURES_PATH, model)   # quiet adaptation (thumb examples)
                if self._wanted:
                    small = cv2.resize(frame, (PREVIEW_WIDTH, int(PREVIEW_WIDTH / aspect)))
                    ok, encoded = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        with self._lock:
                            self._frame = encoded.tobytes()
                time.sleep(max(0.0, 1 / FPS - (time.monotonic() - tick)))
        finally:
            capture.release()
            landmarker.close()
            self.running = False
            with self._lock:
                self._frame = None
            self.on_hold(None, 0.0)
            self.on_state({"on": False, "problem": self.problem})


def _draw(frame, landmarks) -> None:
    import cv2
    h, w = frame.shape[:2]
    points = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for a, b in ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
                 (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)):
        cv2.line(frame, points[a], points[b], (87, 119, 217), 3)
    for point in points:
        cv2.circle(frame, point, 4, (255, 255, 255), -1)


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m choose.camera   (prints signals for 20 seconds)
    problem = Camera.available()
    if problem:
        raise SystemExit(problem)
    camera = Camera(on_signal=lambda s: print("SIGNAL", s) or True,
                    on_state=lambda s: print("camera", s))
    camera.want(True)
    print("Raise a hand or show a thumb for about 1 second. Stops after 20 s.")
    time.sleep(20)
    camera.want(False)
