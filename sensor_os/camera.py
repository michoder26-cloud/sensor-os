"""Webcam reader on a background thread that always holds only the newest frame."""
import sys
import threading
import time

import cv2


# The capture systems to try, best first: (OpenCV API, ask for MJPG).
# On Windows, Media Foundation first: measured on this laptop's camera in the same light,
# DirectShow gave 16.9 fps whatever was asked, Media Foundation 30.3 fps. Twice the frames means
# half the wait for each new hand position and much less motion blur, so hands are lost less.
if sys.platform == "win32":
    TRIES = ((cv2.CAP_MSMF, False), (cv2.CAP_DSHOW, True), (cv2.CAP_DSHOW, False))
elif sys.platform == "darwin":
    TRIES = ((cv2.CAP_AVFOUNDATION, False),)
else:
    TRIES = ((cv2.CAP_V4L2, True), (cv2.CAP_V4L2, False))


def _open(index: int, width: int, height: int, fps: int, api: int, mjpg: bool):
    cap = cv2.VideoCapture(index, api)
    if not cap.isOpened():
        return None
    if mjpg:
        # MJPG is what lets many webcams deliver more than 30 fps.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    for _ in range(40):                      # make sure frames really arrive
        ok, frame = cap.read()
        if ok and frame is not None and frame.size:
            return cap
        time.sleep(0.05)
    cap.release()
    return None


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 480, fps: int = 60):
        self.cap = None
        for api, mjpg in TRIES:
            self.cap = _open(index, width, height, fps if mjpg else 30, api, mjpg)
            if self.cap is not None:
                break
        if self.cap is None:
            cap = cv2.VideoCapture(index)          # whatever OpenCV picks by itself
            self.cap = cap if cap.isOpened() else None
        if self.cap is None:
            raise RuntimeError(f"Cannot open camera {index}")
        self._frame = None
        self._seq = 0
        self.stamp = 0.0                     # perf_counter() when the newest frame arrived
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self._cond:
                self.stamp = time.perf_counter()
                self._frame = frame
                self._seq += 1
                self._cond.notify_all()

    def read(self, last_seq: int = -1, timeout: float = 1.0):
        """Wait for a frame newer than last_seq. Returns (seq, frame) or (last_seq, None)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq != last_seq or not self._running, timeout)
            if self._frame is None or self._seq == last_seq:
                return last_seq, None
            return self._seq, self._frame

    def close(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=1.0)
        self.cap.release()
