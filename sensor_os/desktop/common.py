"""Pieces shared by the desktop backends: window info, plain fallbacks, and input through pynput.

Every backend works in one coordinate space of its own (Windows: physical pixels, macOS: points,
X11: pixels), measured from the top-left corner of the primary screen. Callers only ever turn
0..1 fractions of screen_size() into that space and back, so they never need to know which one it is.
"""
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class WinInfo:
    hwnd: int
    title: str
    rect: tuple          # visible frame (l, t, r, b); for a minimized window, where it will come back
    source: tuple        # visible frame inside the full window rect, for thumbnails
    minimized: bool
    maximized: bool


# ---------------------------------------------------------------- pure geometry, shared and tested
def snap_rect(direction: str, screen: tuple) -> tuple | None:
    """Where a window thrown by its title bar lands: left / right half, or None for up (maximize) / down (minimize)."""
    left, top, right, bottom = screen
    mid = (left + right) // 2
    if direction == "left":
        return left, top, mid, bottom
    if direction == "right":
        return mid, top, right, bottom
    return None


def throw_direction(vx: float, vy: float) -> str:
    if abs(vx) >= abs(vy):
        return "right" if vx > 0 else "left"
    return "down" if vy > 0 else "up"


def on_title_bar(point: tuple, frame: tuple | None, height: float) -> bool:
    """A point within the top strip of a window's frame (the title bar on every common desktop)."""
    if frame is None:
        return False
    x, y = point
    left, top, right, bottom = frame
    return left <= x < right and top <= y < min(top + height, bottom)


def hit(windows: list, x: float, y: float):
    """Front-most window that is not minimized and contains (x, y). `windows` is front to back."""
    for info in windows:
        l, t, r, b = info.rect
        if not info.minimized and l <= x < r and t <= y < b:
            return info.hwnd
    return None


class WheelAccumulator:
    """Windows counts the wheel in 1/120 notches; pynput only takes whole steps. Keep the remainder."""

    def __init__(self, per_step: float = 120.0):
        self.per_step = per_step
        self.x = self.y = 0.0

    def add(self, dx: float, dy: float) -> tuple:
        self.x += dx / self.per_step
        self.y += dy / self.per_step
        sx, sy = int(self.x), int(self.y)
        self.x -= sx
        self.y -= sy
        return sx, sy


# ---------------------------------------------------------------- fallbacks for what an OS cannot do
class NoThumbnail:
    """Live window previews exist only on Windows (DWM). The stage then shows the title on the card."""
    ok = False

    def __init__(self, dest_hwnd=None, source_hwnd=None):
        pass

    def show(self, dest, source=None, opacity: int = 255) -> None:
        pass

    def close(self) -> None:
        pass


class PlainSkin:
    """Hologram dressing for desktops that do not let one program fade another's windows.

    Windows only get turned into floating cards (and maximized again on the way out)."""

    def __init__(self, desk, alpha: int = 255):
        self.desk = desk
        self.maxed: list = []

    def apply(self, infos) -> None:
        pass

    def hide_icons(self) -> None:
        pass

    def hide_taskbar(self) -> None:
        pass                      # macOS Dock / Linux panels are left alone

    def cardify(self, infos) -> None:
        sw, sh = self.desk.screen_size()
        n = 0
        for info in infos:
            if info.minimized or not info.maximized or info.hwnd in self.maxed:
                continue
            self.maxed.append(info.hwnd)
            w, h = int(sw * 0.56), int(sh * 0.64)
            x, y = int(sw * 0.24) + n * 56, int(sh * 0.10) + n * 44
            self.desk.place(info.hwnd, (x, y, x + w, y + h))
            n += 1

    def restore(self) -> None:
        for hwnd in self.maxed:
            try:
                self.desk.maximize(hwnd)
            except Exception:
                pass
        self.maxed = []

    @staticmethod
    def recover() -> int:
        return 0


def attach_to_desktop(hwnd) -> bool:
    return False          # the camera wallpaper then sits just below every other window


def refresh_wallpaper() -> None:
    pass


def make_dpi_aware() -> None:
    pass                  # Qt handles scaling on macOS and Linux


def run_quiet(*args, timeout: float = 2.0) -> str:
    """Run a small helper program; '' if it is missing or fails."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def open_detached(*args) -> None:
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# ---------------------------------------------------------------- input through pynput (macOS and X11)
class PynputInput:
    """Mouse and keys for macOS and X11. Subclasses fill in zoom and the screen geometry."""
    title_height = 32.0

    def __init__(self, desk):
        from pynput import keyboard, mouse
        self.desk = desk
        self._mouse = mouse.Controller()
        self._keys = keyboard.Controller()
        self._Button, self._Key = mouse.Button, keyboard.Key
        self._wheel = WheelAccumulator()
        self.origin = (0, 0)          # where the primary screen starts in pynput's coordinates

    def screen(self) -> tuple:
        return self.desk.screen_size()

    def bounds(self) -> tuple:
        w, h = self.desk.screen_size()
        return 0, 0, w, h

    def cursor(self) -> tuple:
        x, y = self._mouse.position
        return x - self.origin[0], y - self.origin[1]

    def set_cursor(self, x: int, y: int) -> None:
        self._mouse.position = (x + self.origin[0], y + self.origin[1])

    def button(self, name: str, down: bool) -> None:
        b = self._Button.left if name == "left" else self._Button.right
        (self._mouse.press if down else self._mouse.release)(b)

    def click(self, name: str) -> None:
        self.button(name, True)
        self.button(name, False)

    def wheel(self, dx: float, dy: float) -> None:
        sx, sy = self._wheel.add(dx, dy)
        if sx or sy:
            self._mouse.scroll(sx, sy)

    def zoom(self, amount: float) -> None:
        pass

    def on_title_bar(self) -> bool:
        x, y = self.cursor()
        hwnd = self.desk.window_at(x, y)
        return on_title_bar((x, y), self.desk.frame_bounds(hwnd) if hwnd else None, self.title_height)

    def snap(self, direction: str) -> bool:
        """Throw the window under the cursor: sideways = half the screen, up = maximize, down = minimize."""
        hwnd = self.desk.window_at(*self.cursor())
        if not hwnd:
            return False
        sw, sh = self.desk.screen_size()
        rect = snap_rect(direction, (0, 0, sw, sh))
        if rect is not None:
            self.desk.place(hwnd, rect)
        elif direction == "up":
            self.desk.maximize(hwnd)
        else:
            self.desk.minimize(hwnd)
        return True

    def media(self, action: str) -> None:
        key = {"play": self._Key.media_play_pause, "next": self._Key.media_next,
               "prev": self._Key.media_previous}.get(action)
        if key is not None:
            self._keys.press(key)
            self._keys.release(key)


def pynput_type(text: str) -> None:
    from pynput.keyboard import Controller
    Controller().type(text)


def platform_note(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def home_file(*parts) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)
