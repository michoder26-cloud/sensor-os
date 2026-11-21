"""Short synthesized sounds so a gesture can be heard to land, without looking at the ring.

which() is pure: it decides what a frame should sound like from the mode change and the commands.
Player writes the tones once as small .wav files and plays them on its own thread
(winsound on Windows, afplay on macOS, pw-play / paplay / aplay on Linux).
"""
import math
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

RATE = 22050

# name: list of (start Hz, end Hz, seconds, loudness) played one after another
TONES = {
    "touch": [(1500, 1500, 0.030, 0.55)],                          # the pinch was seen
    "click": [(1050, 780, 0.045, 1.0)],
    "right": [(820, 820, 0.040, 0.9), (0, 0, 0.035, 0), (620, 620, 0.055, 0.9)],
    "grab": [(240, 150, 0.090, 1.0)],                              # picked something up
    "drop": [(170, 230, 0.060, 0.7)],
    "throw": [(950, 180, 0.190, 0.9)],
    "pull": [(230, 900, 0.170, 0.9)],                              # a window flies to the hand
    "open": [(700, 700, 0.055, 0.8), (1050, 1050, 0.085, 0.8)],
    "sweep": [(1300, 260, 0.260, 0.8)],                            # clap: everything put away / back
    "tick": [(2100, 2100, 0.012, 0.45)],                           # one step of a dial
}
PRIORITY = ("sweep", "throw", "pull", "open", "right", "click", "grab", "drop", "touch")

TOUCH = ("press", "hub", "prop_rot")
CARRY = ("grab", "pull", "prop_move", "scale", "prop_scale")


def which(prev_mode: str, mode: str, cmds, fling_speed: float):
    """The one sound for this frame, or None."""
    found = set()
    for cmd in cmds:
        kind = cmd[0]
        if kind == "click":
            found.add("click")
        elif kind == "right_click":
            found.add("right")
        elif kind in ("clear", "unclear"):
            found.add("sweep")
        elif kind in ("hub_window", "hub_open"):
            found.add("open")
        elif kind == "pull_start":
            found.add("pull")
        elif kind in ("grab_end", "drag_end") and math.hypot(cmd[1], cmd[2]) >= fling_speed:
            found.add("throw")
    if mode != prev_mode:
        if mode in TOUCH and prev_mode == "free":
            found.add("touch")
        elif mode in CARRY or mode == "drag":
            found.add("grab")
        elif prev_mode in CARRY or prev_mode == "drag":
            found.add("drop")
    return next((name for name in PRIORITY if name in found), None)


def synth(parts, volume: float) -> bytes:
    """16-bit mono samples: each part glides from one pitch to another and fades out."""
    out, phase = [], 0.0
    for f0, f1, seconds, loud in parts:
        n = max(1, int(seconds * RATE))
        for i in range(n):
            k = i / n
            phase += 2 * math.pi * (f0 + (f1 - f0) * k) / RATE
            env = min(1.0, i / (0.004 * RATE)) * (1.0 - k) ** 1.5       # tiny attack, smooth decay: no clicks
            out.append(int(32767 * volume * loud * env * math.sin(phase)) if f0 else 0)
    return struct.pack(f"<{len(out)}h", *out)


class Player:
    def __init__(self, volume: float = 0.3, enabled: bool = True):
        self.enabled = enabled
        self.files: dict = {}
        self._queue: queue.Queue = queue.Queue(maxsize=4)
        if not enabled:
            return
        try:
            folder = Path(tempfile.gettempdir()) / "sensor-os-sounds"
            folder.mkdir(exist_ok=True)
            for name, parts in TONES.items():
                path = folder / f"{name}.wav"
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(RATE)
                    w.writeframes(synth(parts, volume))
                self.files[name] = str(path)
            self._play = self._player()
            if self._play is None:
                raise OSError("no sound player found (install pulseaudio-utils or alsa-utils)")
            threading.Thread(target=self._run, daemon=True, name="sounds").start()
        except Exception as exc:                 # no audio device, no player...: stay silent
            print(f"Sounds unavailable: {exc}", flush=True)
            self.enabled = False

    @staticmethod
    def _player():
        """A function that starts playing a .wav file and returns at once, or None."""
        if sys.platform == "win32":
            import winsound
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            return lambda path: winsound.PlaySound(path, flags)
        for cmd in (["afplay"], ["pw-play"], ["paplay"], ["aplay", "-q"]):      # macOS, PipeWire, PulseAudio, ALSA
            if shutil.which(cmd[0]):
                return lambda path, cmd=cmd: subprocess.Popen(cmd + [path], stdout=subprocess.DEVNULL,
                                                              stderr=subprocess.DEVNULL)
        return None

    def play(self, name) -> None:
        if self.enabled and name in self.files:
            try:
                self._queue.put_nowait(name)
            except queue.Full:
                pass                             # too many at once: skipping one is better than lagging

    def _run(self) -> None:
        while True:
            name = self._queue.get()
            try:
                self._play(self.files[name])
            except (RuntimeError, OSError):
                pass
