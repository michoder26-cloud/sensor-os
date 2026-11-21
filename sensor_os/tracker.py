"""MediaPipe Hand Landmarker wrapper: BGR frame in, Hand objects out."""
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

from .gestures import Hand

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"


def ensure_model(path: Path = MODEL_PATH) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading hand model to {path} ...")
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


class HandTracker:
    def __init__(self, max_hands: int = 1, model_path: Path | None = None):
        options = vision.HandLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(ensure_model(model_path or MODEL_PATH))),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ms = -1
        self._primary = None   # last palm position of the hand in control

    def process(self, frame_bgr, t: float) -> list[Hand]:
        """Returns the visible hands, the one in control first."""
        ms = int(t * 1000)
        if ms <= self._last_ms:
            ms = self._last_ms + 1
        self._last_ms = ms
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, ms)
        hands = [
            Hand(lm=[(p.x, p.y) for p in lm], world=[(p.x, p.y, p.z) for p in world])
            for lm, world in zip(result.hand_landmarks, result.hand_world_landmarks)
        ]
        if not hands:
            self._primary = None
            return hands
        if self._primary is not None and len(hands) > 1:
            # Keep the same hand in control when a second one shows up.
            px, py = self._primary
            hands.sort(key=lambda h: (h.lm[0][0] - px) ** 2 + (h.lm[0][1] - py) ** 2)
        self._primary = hands[0].lm[0]
        return hands

    def close(self):
        self._landmarker.close()
