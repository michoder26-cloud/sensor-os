"""The machine behind the floating controls: speaker volume, screen brightness, media keys, CPU / RAM / battery.

Everything slow (COM, osascript, pactl) runs on this thread so the hand tracking never waits for it.
`state` is what the controls show; request() is what the hands asked for.
Each part is optional: a desktop PC has no brightness or battery, and that control simply reads "n/a".

    volume      Windows: pycaw   macOS: osascript   Linux: pactl (PulseAudio / PipeWire) or amixer (ALSA)
    brightness  Windows and Linux: screen-brightness-control   macOS: not available
    media keys  Windows: keybd_event   macOS and Linux: pynput
"""
import re
import shutil
import subprocess
import sys
import threading
import time

POLL = 1.0          # seconds between readings of the machine's state
BRIGHTNESS_POLL = 5.0   # brightness is read through WMI on Windows, which is slow: less often


def _run(*args) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=2.0).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- volume, one class per OS
class WinVolume:
    def __init__(self):
        import comtypes
        comtypes.CoInitialize()
        from pycaw.pycaw import AudioUtilities
        self.ep = AudioUtilities.GetSpeakers().EndpointVolume

    def read(self) -> tuple:
        return float(self.ep.GetMasterVolumeLevelScalar()), bool(self.ep.GetMute())

    def set(self, level: float) -> None:
        self.ep.SetMasterVolumeLevelScalar(level, None)
        if level > 0 and self.ep.GetMute():
            self.ep.SetMute(0, None)                # turning it up unmutes

    def toggle_mute(self) -> None:
        self.ep.SetMute(0 if self.ep.GetMute() else 1, None)


class MacVolume:
    def __init__(self):
        if not shutil.which("osascript"):
            raise OSError("osascript not found")

    def read(self) -> tuple:
        out = _run("osascript", "-e", "set s to get volume settings",
                   "-e", "return (output volume of s as text) & \",\" & (output muted of s as text)")
        level, _, muted = out.strip().partition(",")
        if not level or level == "missing value":
            raise OSError("this output device has no volume")
        return int(level) / 100.0, muted.strip() == "true"

    def set(self, level: float) -> None:
        _run("osascript", "-e", f"set volume output volume {round(level * 100)} without output muted")

    def toggle_mute(self) -> None:
        _, muted = self.read()
        _run("osascript", "-e", f"set volume output muted {'false' if muted else 'true'}")


def parse_pactl_volume(text: str):
    """First channel's percentage from `pactl get-sink-volume`, 0..1 (can exceed 1 when boosted)."""
    m = re.search(r"(\d+)%", text)
    return int(m.group(1)) / 100.0 if m else None


class PulseVolume:
    SINK = "@DEFAULT_SINK@"

    def __init__(self):
        if not shutil.which("pactl"):
            raise OSError("pactl not found")
        self.read()

    def read(self) -> tuple:
        level = parse_pactl_volume(_run("pactl", "get-sink-volume", self.SINK))
        if level is None:
            raise OSError("no default audio output")
        return min(level, 1.0), "yes" in _run("pactl", "get-sink-mute", self.SINK).lower()

    def set(self, level: float) -> None:
        _run("pactl", "set-sink-volume", self.SINK, f"{round(level * 100)}%")
        if level > 0:
            _run("pactl", "set-sink-mute", self.SINK, "0")

    def toggle_mute(self) -> None:
        _run("pactl", "set-sink-mute", self.SINK, "toggle")


class AlsaVolume:
    def __init__(self):
        if not shutil.which("amixer"):
            raise OSError("amixer not found")
        self.read()

    def read(self) -> tuple:
        out = _run("amixer", "get", "Master")
        level = parse_pactl_volume(out)
        if level is None:
            raise OSError("no Master control")
        return level, "[off]" in out

    def set(self, level: float) -> None:
        _run("amixer", "-q", "set", "Master", f"{round(level * 100)}%", "unmute" if level > 0 else "")

    def toggle_mute(self) -> None:
        _run("amixer", "-q", "set", "Master", "toggle")


def parse_netsh(text: str):
    """(network name, signal 0..1) from `netsh wlan show interfaces`, or None when not connected."""
    name, level = None, None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "SSID" and value:
            name = value
        elif value.endswith("%") and value[:-1].strip().isdigit() and level is None:
            level = int(value[:-1]) / 100.0
    return (name, level) if name else None


def parse_nmcli(text: str):
    """From `nmcli -t -f ACTIVE,SSID,SIGNAL dev wifi`: the line that is in use."""
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == "yes":
            return parts[1], int(parts[2]) / 100.0 if parts[2].isdigit() else None
    return None


def read_wifi():
    if sys.platform == "win32":
        return parse_netsh(_run("netsh", "wlan", "show", "interfaces"))
    if sys.platform == "darwin":
        out = _run("networksetup", "-getairportnetwork", "en0")
        return (out.split(":", 1)[1].strip(), None) if ":" in out and "not associated" not in out else None
    return parse_nmcli(_run("nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"))


def open_volume():
    if sys.platform == "win32":
        choices = (WinVolume,)
    elif sys.platform == "darwin":
        choices = (MacVolume,)
    else:
        choices = (PulseVolume, AlsaVolume)
    errors = []
    for cls in choices:
        try:
            return cls()
        except Exception as exc:
            errors.append(f"{cls.__name__}: {exc}")
    print(f"Volume control unavailable: {'; '.join(errors)}", flush=True)
    return None


def open_brightness():
    if sys.platform == "darwin":
        return None                  # macOS gives no public way to set the built-in display's brightness
    try:
        import screen_brightness_control as sbc
        return sbc if sbc.get_brightness() else None
    except Exception:
        return None                  # a monitor without software brightness: the dial reads n/a


def media_key(action: str) -> None:
    if sys.platform == "win32":
        import ctypes
        vk = {"next": 0xB0, "prev": 0xB1, "play": 0xB3}.get(action)
        if vk is not None:
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)
        return
    from pynput.keyboard import Controller, Key
    key = {"play": Key.media_play_pause, "next": Key.media_next, "prev": Key.media_previous}.get(action)
    if key is not None:
        keys = Controller()
        keys.press(key)
        keys.release(key)


class SystemControls(threading.Thread):
    def __init__(self, live: bool = True):
        super().__init__(daemon=True, name="system-controls")
        self.live = live                    # False (test mode): read the machine but never change it
        self.state = {"volume": None, "muted": False, "brightness": None,
                      "cpu": 0.0, "ram": 0.0, "battery": None, "plugged": None, "wifi": None}
        self._lock = threading.Lock()
        self._levels: dict = {}             # latest wanted level per dial; older ones are dropped
        self._actions: list = []
        self._wake = threading.Event()

    # ---------------------------------------------------------------- from the hand-tracking thread
    def request(self, name: str, action) -> None:
        with self._lock:
            if isinstance(action, str):
                self._actions.append((name, action))
            else:
                self._levels[name] = float(action)
                self.state = {**self.state, name: float(action)}      # show it at once
        self._wake.set()

    # ---------------------------------------------------------------- this thread
    def run(self) -> None:
        volume = open_volume()
        sbc = open_brightness()
        try:
            import psutil
            psutil.cpu_percent(None)
        except Exception:
            psutil = None
        last_poll = last_brightness = last_wifi = -1e9
        while True:
            self._wake.wait(0.05 if self._levels else POLL)
            self._wake.clear()
            with self._lock:
                levels, self._levels = self._levels, {}
                actions, self._actions = self._actions, []
            fresh = {}
            try:
                if self.live:
                    if "volume" in levels and volume is not None:
                        volume.set(levels["volume"])
                    if "brightness" in levels and sbc is not None:
                        sbc.set_brightness(round(levels["brightness"] * 100))
                    for name, action in actions:
                        if name == "volume" and action == "mute" and volume is not None:
                            volume.toggle_mute()
                            fresh["muted"] = volume.read()[1]
                        elif name == "media":
                            media_key(action)
                now = time.monotonic()
                if now - last_poll >= POLL:
                    last_poll = now
                    if volume is not None:
                        level, fresh["muted"] = volume.read()
                        if "volume" not in levels:
                            fresh["volume"] = level
                    if sbc is not None and "brightness" not in levels and now - last_brightness >= BRIGHTNESS_POLL:
                        last_brightness = now
                        got = sbc.get_brightness()
                        if got:
                            fresh["brightness"] = got[0] / 100.0
                    if now - last_wifi >= 5.0:
                        last_wifi = now
                        fresh["wifi"] = read_wifi()
                    if psutil is not None:
                        fresh["cpu"] = psutil.cpu_percent(None) / 100.0
                        fresh["ram"] = psutil.virtual_memory().percent / 100.0
                        battery = psutil.sensors_battery()
                        if battery is not None:
                            fresh["battery"], fresh["plugged"] = battery.percent / 100.0, bool(battery.power_plugged)
            except Exception as exc:            # a device that went away (headphones unplugged...): keep going
                print(f"System control failed: {exc}", flush=True)
                volume = open_volume()
            if fresh:
                with self._lock:
                    for key in self._levels:    # a newer request from the hand wins over a reading
                        fresh.pop(key, None)
                    self.state = {**self.state, **fresh}
