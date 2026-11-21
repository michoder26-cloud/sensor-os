"""The operating system's desktop: windows, mouse and keyboard, hotkeys, opening files, the app list.

One backend per OS, picked here:
    windows   Windows 10 / 11                       everything, including see-through windows and live previews
    mac       macOS (Quartz + Accessibility)        no see-through windows, no live previews
    x11       Linux on an X11 session               no see-through windows, no live previews
    null      Wayland, or anything else            the hand tracking runs, but nothing on the desktop moves

Force one with the environment variable SENSOR_OS_DESKTOP (windows, mac, x11 or null).
All of them offer the same names; see null.py for the full list with what each does.
"""
import os
import sys


def pick(platform: str = sys.platform, env=None) -> str:
    env = os.environ if env is None else env
    forced = env.get("SENSOR_OS_DESKTOP", "").strip().lower()
    if forced in ("windows", "mac", "x11", "null"):
        return forced
    if platform == "win32":
        return "windows"
    if platform == "darwin":
        return "mac"
    if platform.startswith("linux"):
        # Wayland does not let one program move another's windows or the mouse.
        if env.get("XDG_SESSION_TYPE", "").lower() == "wayland" or (env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY")):
            return "null"
        return "x11" if env.get("DISPLAY") else "null"
    return "null"


def _load(name: str):
    if name == "windows":
        from . import win as backend
    elif name == "mac":
        from . import mac as backend
    elif name == "x11":
        from . import x11 as backend
    else:
        from . import null as backend
    return backend


_wanted = pick()
try:
    _backend = _load(_wanted)
except ImportError as exc:      # a missing package (pyobjc, python-xlib, pynput...): still start, but say so
    print(f"Desktop control for '{_wanted}' is unavailable ({exc}). "
          f"Install the packages in requirements.txt. Running without desktop control.", file=sys.stderr, flush=True)
    from . import null as _backend

NAME = _backend.NAME
FEATURES = _backend.FEATURES
WinInfo = _backend.WinInfo
screen_size = _backend.screen_size
list_windows = _backend.list_windows
window_at = _backend.window_at
frame_bounds = _backend.frame_bounds
place = _backend.place
minimize = _backend.minimize
maximize = _backend.maximize
restore = _backend.restore
focus = _backend.focus
Thumbnail = _backend.Thumbnail
HoloSkin = _backend.HoloSkin
attach_to_desktop = _backend.attach_to_desktop
refresh_wallpaper = _backend.refresh_wallpaper
make_dpi_aware = _backend.make_dpi_aware
make_input = _backend.make_input
start_hotkeys = _backend.start_hotkeys
hotkey_down = _backend.hotkey_down
open_path = _backend.open_path
is_hidden = _backend.is_hidden
app_list = _backend.app_list
power = _backend.power
type_text = _backend.type_text
close_window = _backend.close_window
