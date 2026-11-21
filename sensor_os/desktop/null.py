"""No desktop control: Wayland, an unknown OS, or a backend whose packages are missing.

The camera, hand tracking, HUD and 3D models all still run, so gestures can be tried out,
but no window or cursor moves. This file also documents what every backend must offer.
"""
import os
import sys
from pathlib import Path

from .common import NoThumbnail as Thumbnail  # noqa: F401
from .common import WinInfo, attach_to_desktop, make_dpi_aware, open_detached, refresh_wallpaper  # noqa: F401

NAME = "null"
FEATURES = {"translucent": False, "thumbnails": False, "wallpaper": False}


def screen_size() -> tuple:
    """Primary screen size in this backend's own units."""
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.geometry()
            return g.width(), g.height()
    except Exception:
        pass
    return 1920, 1080


def list_windows(exclude_pid=None) -> list:
    """Normal application windows on the primary screen, front-most first (WinInfo)."""
    return []


def window_at(x, y):
    """Window handle under a point, or None."""
    return None


def frame_bounds(hwnd):
    """Visible frame (l, t, r, b) of a window, or None."""
    return None


def place(hwnd, rect) -> bool:
    """Move / resize so the visible frame lands exactly on rect."""
    return False


def minimize(hwnd) -> bool:
    return False


def maximize(hwnd) -> bool:
    return False


def restore(hwnd) -> bool:
    return False


def focus(hwnd) -> bool:
    return False


class HoloSkin:
    def __init__(self, alpha: int = 255):
        pass

    def apply(self, infos) -> None:
        pass

    def cardify(self, infos) -> None:
        pass

    def hide_icons(self) -> None:
        pass

    def hide_taskbar(self) -> None:
        pass

    def restore(self) -> None:
        pass

    @staticmethod
    def recover() -> int:
        return 0


class NullInput:
    def screen(self):
        return screen_size()

    def bounds(self):
        w, h = screen_size()
        return 0, 0, w, h

    def cursor(self):
        return 0, 0

    def set_cursor(self, x, y):
        pass

    def button(self, name, down):
        pass

    def click(self, name):
        pass

    def wheel(self, dx, dy):
        pass

    def zoom(self, amount):
        pass

    def on_title_bar(self):
        return False

    def snap(self, direction):
        return False

    def media(self, action):
        pass


def make_input():
    print("Desktop control is off on this system: gestures are shown but nothing is clicked or moved.",
          file=sys.stderr, flush=True)
    return NullInput()


def start_hotkeys() -> bool:
    return False


def hotkey_down(letter: str) -> bool:
    return False


def open_path(path: str) -> None:
    if sys.platform == "darwin":
        open_detached("open", path)
    elif os.name == "nt":
        os.startfile(path)
    else:
        open_detached("xdg-open", path)


def is_hidden(path: Path) -> bool:
    return path.name.startswith(".")


def app_list() -> list:
    return []


def power(action: str) -> bool:
    return False


def type_text(text: str) -> None:
    pass


def close_window(hwnd) -> bool:
    return False
