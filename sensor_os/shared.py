"""Thread-safe state passed from the tracking thread to the HUD."""
import dataclasses
import threading
from dataclasses import dataclass


@dataclass
class Snapshot:
    mode: str = "lost"          # GestureEngine.mode
    progress: float = 0.0       # 0 = fingers apart, 1 = pinched
    hold: float = 0.0           # 0..1 hold-for-right-click progress
    scroll_axis: str = "y"
    lm: list | None = None      # 21 (x, y) of the hand in control
    fps: float = 0.0
    enabled: bool = True
    dry_run: bool = False
    click_seq: int = 0          # bumped on every click (HUD ripple)
    rclick_seq: int = 0
    help_seq: int = 0           # bumped to show / hide the help card
    banner: tuple | None = None # (title, subtitle, progress 0..1 or None)
    summon: float = 0.0         # 0..1 progress of the raise-both-hands gesture
    jarvis: dict | None = None  # the voice assistant: state, what it heard, what it said
    glare: str | None = None    # "left" / "middle" / "right": that part of the camera picture is blown out
    stage: dict | None = None   # StageEngine.view() while the stage is open, else None
    holo: dict | None = None    # HoloEngine.view() while hologram mode is on, else None
    frame: object = None        # latest mirrored camera frame (stage background)
    crop: tuple = (0.0, 0.0, 1.0, 1.0)
    finished: bool = False
    error: str | None = None


class Shared:
    def __init__(self):
        self._lock = threading.Lock()
        self._s = Snapshot()
        self._keys: list = []        # keyboard text from the HUD, for the note being typed into

    def update(self, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._s, key, value)

    def bump(self, name: str) -> None:
        with self._lock:
            setattr(self._s, name, getattr(self._s, name) + 1)

    def push_key(self, text: str) -> None:
        with self._lock:
            self._keys.append(text)

    def take_keys(self) -> str:
        with self._lock:
            out, self._keys = "".join(self._keys), []
            return out

    def get(self) -> Snapshot:
        with self._lock:
            return dataclasses.replace(self._s)
