"""JARVIS's ears and voice.

Ears: the microphone streams 80 ms chunks. A small wake-word model ("Hey Jarvis", openWakeWord)
runs on this PC and scores each chunk; no sound leaves the PC until it fires. Then one utterance is
recorded until you stop talking and sent to Google's speech recognition (Thai) to become text.
Voice: Microsoft's neural Thai voice (edge-tts) makes the speech; it is played through the OS.
While JARVIS speaks the microphone is ignored, so it never answers itself.
"""
import asyncio
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

from .speech_text import T

RATE = 16000
CHUNK = 1280                     # 80 ms: what the wake-word model expects


NAME_SHORTEST, NAME_LONGEST = 0.3, 2.5   # seconds: a phrase this long, said on its own, may be the name
NAME_PAUSE = 0.45                         # seconds of quiet that end it
NAMES = ("จาร์วิส", "จาวิส", "จารวิส", "จาร์วิด", "จาวิด", "ชาร์วิส", "ชาวิส", "จาร์วิซ", "จาวิซ", "จาร์วิช", "จาวิช",
         "จาร์วิท", "จาวิท", "jarvis", "javis", "jervis", "jarvi", "charis", "jarvas")   # as Google wrote it here


def after_name(text: str) -> str | None:
    """None if the name is not in what was heard; else what came after it ("" if nothing)."""
    t = (text or "").strip()
    low = t.lower()
    squeezed = low.replace(" ", "")
    for name in NAMES:
        if name in squeezed:
            k = low.find(name)
            if k < 0:                                  # "จา วิส": found only with the spaces taken out
                return ""
            rest = t[k + len(name):].strip(" ,.!?")
            for lead in ("ครับ", "คะ", "ค่ะ", "จ้า"):
                rest = rest.removeprefix(lead).strip()
            return rest
    return None


def rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) if len(chunk) else 0.0


class Segmenter:
    """Cuts one spoken request out of a stream of chunks: starts at the first speech, ends after a
    pause. Pure, so it is tested with made-up audio."""

    def __init__(self, noise: float = 200.0, pause: float = 0.8, longest: float = 12.0, wait: float = 5.0):
        self.noise = max(noise, 60.0)
        self.pause, self.longest, self.wait = pause, longest, wait
        self.chunks, self.started, self.quiet, self.t = [], False, 0.0, 0.0

    def feed(self, chunk: np.ndarray) -> str:
        """Returns "more", "done" (a request was heard) or "nothing" (no one spoke)."""
        dt = len(chunk) / RATE
        self.t += dt
        loud = rms(chunk) > self.noise * 2.5
        if not self.started:
            if loud:
                self.started = True
                self.chunks.append(chunk)
            elif self.t >= self.wait:
                return "nothing"
            else:
                self.noise = 0.95 * self.noise + 0.05 * max(rms(chunk), 60.0)   # learn the room's hum
            return "more"
        self.chunks.append(chunk)
        self.quiet = 0.0 if loud else self.quiet + dt
        if self.quiet >= self.pause or self.t >= self.longest:
            return "done"
        return "more"

    def audio(self) -> bytes:
        return np.concatenate(self.chunks).astype(np.int16).tobytes() if self.chunks else b""


class Ears:
    """Microphone + wake word. listen_for_wake() blocks until "Hey Jarvis" (or wake() is called);
    hear() records one request and returns its text."""

    def __init__(self, threshold: float = 0.5, language: str = "th-TH", name_check: bool = True):
        import sounddevice as sd
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        download_models(model_names=["hey_jarvis"])
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.threshold, self.language, self.name_check = threshold, language, name_check
        self.first_request = None
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.muted = threading.Event()             # set while JARVIS speaks
        self.forced = threading.Event()            # Ctrl+Alt+J: listen now, no wake word
        self.level = 0.0                           # loudness now, for the screen
        self.noise = 200.0
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                     callback=self._audio)
        self.stream.start()

    def _audio(self, data, frames, time_info, status):
        chunk = data[:, 0].copy()
        self.level = rms(chunk)
        if self.muted.is_set():
            return
        try:
            self.q.put_nowait(chunk)
        except queue.Full:
            pass

    def wake(self) -> None:
        self.forced.set()

    def drain(self) -> None:
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def listen_for_wake(self, stop: threading.Event) -> bool:
        """True when called by name (or forced); False when stopping.

        The wake-word model only knows "Hey Jarvis": the name said on its own the Thai way ("จาร์วิส")
        scores 0, so it never woke. A short phrase said on its own (like a name) is therefore also checked
        with the speech recognizer, which hears "จาร์วิส" fine; "จาร์วิส เปิด YouTube" in one breath
        wakes JARVIS and goes straight in as the request. (Those short phrases go to Google to be checked.)"""
        self.model.reset()
        self.first_request = None
        spoken, before, quiet, peak = [], [], 0.0, 0.0
        while not stop.is_set():
            if self.forced.is_set():
                self.forced.clear()
                return True
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            score = max(self.model.predict(chunk).values())
            if score >= self.threshold:
                self.model.reset()
                print(f"JARVIS woke: Hey Jarvis (sure {score:.2f})", flush=True)
                return True
            loud = rms(chunk) > self.noise * 2.5
            if not spoken and not loud:
                self.noise = 0.99 * self.noise + 0.01 * max(rms(chunk), 60.0)
                before = (before + [chunk])[-4:]                  # a moment before the voice starts
                continue
            if not spoken:
                spoken = list(before)
            spoken.append(chunk)
            peak = max(peak, score)
            quiet = 0.0 if loud else quiet + len(chunk) / RATE
            length = len(spoken) * CHUNK / RATE
            if quiet < NAME_PAUSE and length < NAME_LONGEST + 1.0:
                continue
            # the phrase is over
            said = length - quiet - len(before) * CHUNK / RATE
            if peak >= 0.1:
                print(f"JARVIS almost woke (sure {peak:.2f}, needs {self.threshold:.2f})", flush=True)
            if self.name_check and NAME_SHORTEST <= said <= NAME_LONGEST:
                woke = self._name_in(np.concatenate(spoken))
                if woke:
                    return True
            spoken, before, quiet, peak = [], [], 0.0, 0.0
        return False

    def _name_in(self, audio: np.ndarray) -> bool:
        try:
            text = self.to_text(audio.astype(np.int16).tobytes())
        except RuntimeError:
            return False
        rest = after_name(text)
        if rest is None:
            return False
        self.model.reset()
        print(f"JARVIS woke: heard its name ({text})", flush=True)
        self.first_request = rest or None
        return True

    def take_first(self) -> str | None:
        """A request said in the same breath as the name ("จาร์วิส เปิด YouTube"), once."""
        text, self.first_request = getattr(self, "first_request", None), None
        return text

    def hear(self, stop: threading.Event, wait: float = 5.0) -> str | None:
        """Record one request and turn it into text. None if nobody spoke; "" if it could not be understood."""
        seg = Segmenter(self.noise, wait=wait)
        while not stop.is_set():
            try:
                chunk = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            state = seg.feed(chunk)
            if state == "nothing":
                return None
            if state == "done":
                return self.to_text(seg.audio())
        return None

    def to_text(self, pcm: bytes) -> str:
        import speech_recognition as sr
        audio = sr.AudioData(pcm, RATE, 2)
        try:
            return sr.Recognizer().recognize_google(audio, language=self.language)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            raise RuntimeError(T("no_speech_service")) from exc

    def close(self) -> None:
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


class Voice:
    """Speaks Thai with Microsoft's neural voice; play() blocks until the sentence is finished."""

    def __init__(self, voice: str = "th-TH-NiwatNeural", ears: Ears | None = None):
        self.voice, self.ears = voice, ears
        self.folder = tempfile.mkdtemp(prefix="jarvis_voice_")
        self.n = 0

    def make(self, text: str, path: str) -> str:
        import edge_tts

        async def run():
            await edge_tts.Communicate(text, self.voice).save(path)

        asyncio.run(run())
        return path

    def ready(self, text: str) -> str | None:
        """A short phrase made once and kept (the "Yes?" said the moment you stop talking)."""
        path = os.path.join(self.folder, f"ready{abs(hash(text)) % 10**8}.mp3")
        if not os.path.exists(path):
            try:
                self.make(text, path)
            except Exception:
                return None
        return path

    def say(self, text: str) -> None:
        if not text.strip():
            return
        self.n += 1
        self.play_file(self.make(text, os.path.join(self.folder, f"say{self.n % 4}.mp3")))

    def play_file(self, path: str | None) -> None:
        if not path:
            return
        if self.ears is not None:
            self.ears.muted.set()
        try:
            play(path)
        finally:
            time.sleep(0.25)                   # the room's echo dies down before listening again
            if self.ears is not None:
                self.ears.drain()
                self.ears.muted.clear()


def play(path: str) -> None:
    if sys.platform == "win32":
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "jarvis_say"
        mci(f"close {alias}", None, 0, 0)
        if mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0) == 0:
            mci(f"play {alias} wait", None, 0, 0)
            mci(f"close {alias}", None, 0, 0)
        return
    for cmd in (["afplay"], ["mpg123", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        try:
            subprocess.run(cmd + [path], check=False)
            return
        except FileNotFoundError:
            continue
