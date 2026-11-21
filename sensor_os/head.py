"""Where the viewer's head is, so floating things can be drawn with depth.

Returns (hx, hy) in -1..1: hx > 0 when the head is to the right of the screen centre,
hy > 0 when it is below. Runs MediaPipe's small face detector on every other frame.
"""
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

from .filters import OneEuroFilter

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "blaze_face_short_range.tflite"


def ensure_model(path: Path = MODEL_PATH) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading face model to {path} ...", flush=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def to_offset(cx: float, cy: float, sensitivity: float) -> tuple:
    """Face centre in the mirrored frame (0..1) -> head offset (-1..1)."""
    clamp = lambda v: max(-1.0, min(1.0, v))
    return clamp((cx - 0.5) * 2 * sensitivity), clamp((cy - 0.5) * 2 * sensitivity)


class HeadTracker:
    def __init__(self, sensitivity: float = 1.6):
        options = vision.FaceDetectorOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(ensure_model())),
            running_mode=vision.RunningMode.VIDEO,
            min_detection_confidence=0.5,
        )
        self._detector = vision.FaceDetector.create_from_options(options)
        self.sensitivity = sensitivity
        self._fx = OneEuroFilter(0.8, 3.0)      # slow and steady: a twitchy viewpoint is worse than a late one
        self._fy = OneEuroFilter(0.8, 3.0)
        self._last_ms = -1
        self._n = 0
        self.head = (0.0, 0.0)
        self._neutral = None                    # where the head was first seen = straight ahead

    def update(self, frame_bgr, t: float) -> tuple:
        self._n += 1
        if self._n % 2:                          # every other frame is plenty for a head
            return self.head
        ms = max(int(t * 1000), self._last_ms + 1)
        self._last_ms = ms
        small = cv2.resize(frame_bgr, (320, 240))
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
        result = self._detector.detect_for_video(image, ms)
        if not result.detections:
            self.head = (self.head[0] * 0.9, self.head[1] * 0.9)     # drift back to straight ahead
            return self.head
        box = max(result.detections, key=lambda d: d.bounding_box.width).bounding_box
        cx = (box.origin_x + box.width / 2) / 320
        cy = (box.origin_y + box.height / 2) / 240
        if self._neutral is None:
            self._neutral = (cx, cy)
        hx, hy = to_offset(cx - self._neutral[0] + 0.5, cy - self._neutral[1] + 0.5, self.sensitivity)
        self.head = (self._fx(hx, t), self._fy(hy, t))
        return self.head

    def close(self):
        self._detector.close()
