"""Linux backend for X11 sessions: windows through python-xlib and the EWMH hints every common
window manager supports (GNOME on Xorg, KDE, Xfce, Cinnamon, MATE, i3...), mouse and keys through pynput.

Not available here: see-through windows of other programs and live window previews.
Wayland sessions cannot be controlled this way at all; log in with "GNOME on Xorg" / "Plasma (X11)".
Coordinates are pixels from the top-left of the primary monitor.
"""
import os
import threading
import time
from pathlib import Path

from .common import NoThumbnail as Thumbnail  # noqa: F401
from .common import (PlainSkin, PynputInput, WinInfo, attach_to_desktop, hit,  # noqa: F401
                     make_dpi_aware, open_detached, refresh_wallpaper, run_quiet)

NAME = "x11"
FEATURES = {"translucent": False, "thumbnails": False, "wallpaper": False}
SKIP_TYPES = {"_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK", "_NET_WM_WINDOW_TYPE_TOOLBAR",
              "_NET_WM_WINDOW_TYPE_MENU", "_NET_WM_WINDOW_TYPE_SPLASH", "_NET_WM_WINDOW_TYPE_NOTIFICATION",
              "_NET_WM_WINDOW_TYPE_DROPDOWN_MENU", "_NET_WM_WINDOW_TYPE_POPUP_MENU", "_NET_WM_WINDOW_TYPE_TOOLTIP",
              "_NET_WM_WINDOW_TYPE_COMBO", "_NET_WM_WINDOW_TYPE_DND", "_NET_WM_WINDOW_TYPE_UTILITY"}
CACHE = 0.15            # seconds a window list is reused (window_at runs every camera frame)


# ---------------------------------------------------------------- pure geometry (tested on any OS)
def visible_frame(client: tuple, net: tuple, gtk: tuple) -> tuple:
    """client = (l, t, r, b) of the client area. net = _NET_FRAME_EXTENTS (left, right, top, bottom): the
    window manager's decoration around it. gtk = _GTK_FRAME_EXTENTS: invisible shadow drawn inside it."""
    l, t, r, b = client
    return (l - net[0] + gtk[0], t - net[2] + gtk[2], r + net[1] - gtk[1], b + net[3] - gtk[3])


def moveresize_args(rect: tuple, net: tuple, gtk: tuple) -> tuple:
    """(x, y, width, height) for _NET_MOVERESIZE_WINDOW with north-west gravity, so that the
    *visible* frame lands on rect: x, y place the outer frame, width / height size the client."""
    l, t, r, b = (int(round(v)) for v in rect)
    return (l - gtk[0], t - gtk[2],
            max(1, (r - l) - net[0] - net[1] + gtk[0] + gtk[1]),
            max(1, (b - t) - net[2] - net[3] + gtk[2] + gtk[3]))


def parse_desktop_entry(text: str):
    """(name, exec) of a .desktop file, or None if it should not be listed."""
    section, values = None, {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "Desktop Entry" or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), value.strip())
    if values.get("Type", "Application") != "Application":
        return None
    if values.get("NoDisplay", "").lower() == "true" or values.get("Hidden", "").lower() == "true":
        return None
    if not values.get("Name") or not values.get("Exec"):
        return None
    return values["Name"], values["Exec"]


# ---------------------------------------------------------------- X connection
class _X:
    def __init__(self):
        from Xlib import X, Xatom, display
        from Xlib.protocol import event
        self.X, self.Xatom, self.event = X, Xatom, event
        self.d = display.Display()
        self.root = self.d.screen().root
        self._atoms: dict = {}
        self.lock = threading.RLock()
        self.origin, self.size = self._primary()

    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.d.intern_atom(name)
        return self._atoms[name]

    def name_of(self, a: int) -> str:
        try:
            return self.d.get_atom_name(a)
        except Exception:
            return ""

    def prop(self, win, name: str, kind=None):
        try:
            p = win.get_full_property(self.atom(name), kind if kind is not None else self.X.AnyPropertyType)
        except Exception:
            return None
        return p.value if p is not None else None

    def window(self, wid: int):
        return self.d.create_resource_object("window", int(wid))

    def send(self, win, name: str, data: list) -> None:
        ev = self.event.ClientMessage(window=win, client_type=self.atom(name), data=(32, (list(data) + [0] * 5)[:5]))
        self.root.send_event(ev, event_mask=self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask)
        self.d.flush()

    def _primary(self):
        g = self.root.get_geometry()
        try:
            res = self.root.xrandr_get_screen_resources()
            out = self.root.xrandr_get_output_primary().output
            if out:
                info = self.d.xrandr_get_output_info(out, res.config_timestamp)
                if info.crtc:
                    c = self.d.xrandr_get_crtc_info(info.crtc, res.config_timestamp)
                    return (c.x, c.y), (c.width, c.height)
        except Exception:
            pass
        return (0, 0), (g.width, g.height)


_x = None


def _conn() -> _X:
    global _x
    if _x is None:
        _x = _X()
    return _x


def screen_size() -> tuple:
    return _conn().size


# ---------------------------------------------------------------- windows
_cache = (0.0, [])
_extents: dict = {}         # wid -> (net, gtk), for placing


def _states(x, win) -> set:
    return {x.name_of(a) for a in (x.prop(win, "_NET_WM_STATE") or [])}


def list_windows(exclude_pid=None) -> list:
    global _cache
    now = time.monotonic()
    if now - _cache[0] < CACHE:
        return list(_cache[1])
    x = _conn()
    own = os.getpid() if exclude_pid is None else exclude_pid
    ox, oy = x.origin
    sw, sh = x.size
    out = []
    with x.lock:
        stack = list(x.prop(x.root, "_NET_CLIENT_LIST_STACKING") or x.prop(x.root, "_NET_CLIENT_LIST") or [])
        for wid in reversed(stack):                  # the stacking list is bottom to top
            try:
                win = x.window(wid)
                pid = (x.prop(win, "_NET_WM_PID") or [0])[0]
                if pid == own:
                    continue
                types = {x.name_of(a) for a in (x.prop(win, "_NET_WM_WINDOW_TYPE") or [])}
                if types & SKIP_TYPES:
                    continue
                states = _states(x, win)
                if "_NET_WM_STATE_SKIP_TASKBAR" in states:
                    continue
                raw = x.prop(win, "_NET_WM_NAME", x.atom("UTF8_STRING"))
                title = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (win.get_wm_name() or "")
                if not title:
                    continue
                g = win.get_geometry()
                pos = x.root.translate_coords(win, 0, 0)
                net = tuple((x.prop(win, "_NET_FRAME_EXTENTS") or [0, 0, 0, 0])[:4])
                gtk = tuple((x.prop(win, "_GTK_FRAME_EXTENTS") or [0, 0, 0, 0])[:4])
                _extents[int(wid)] = (net, gtk)
                l, t, r, b = visible_frame((pos.x, pos.y, pos.x + g.width, pos.y + g.height), net, gtk)
                rect = (l - ox, t - oy, r - ox, b - oy)
                if r - l < 40 or b - t < 30:
                    continue
                minimized = "_NET_WM_STATE_HIDDEN" in states
                cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
                if not minimized and not (0 <= cx < sw and 0 <= cy < sh):
                    continue                          # lives on another monitor
                maximized = {"_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"} <= states
                out.append(WinInfo(int(wid), title, rect, (0, 0, rect[2] - rect[0], rect[3] - rect[1]),
                                   minimized, maximized))
            except Exception:
                continue                              # a window that closed while we looked
    _cache = (now, out)
    return list(out)


def _forget() -> None:
    global _cache
    _cache = (0.0, [])


def window_at(px, py):
    return hit(list_windows(), px, py)


def frame_bounds(hwnd):
    return next((w.rect for w in list_windows() if w.hwnd == hwnd), None)


def place(hwnd, rect) -> bool:
    x = _conn()
    with x.lock:
        try:
            win = x.window(hwnd)
            states = _states(x, win)
            if "_NET_WM_STATE_HIDDEN" in states:
                x.send(win, "_NET_ACTIVE_WINDOW", [2, 0, 0])
            if states & {"_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"}:
                x.send(win, "_NET_WM_STATE", [0, x.atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                                              x.atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 2])
            net, gtk = _extents.get(int(hwnd), ((0, 0, 0, 0), (0, 0, 0, 0)))
            ox, oy = x.origin
            l, t, r, b = rect
            px, py, w, h = moveresize_args((l + ox, t + oy, r + ox, b + oy), net, gtk)
            flags = 1 | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (2 << 12)   # north-west, x y w h, from a pager
            x.send(win, "_NET_MOVERESIZE_WINDOW", [flags, px, py, w, h])
            _forget()
            return True
        except Exception:
            return False


def minimize(hwnd) -> bool:
    x = _conn()
    with x.lock:
        try:
            x.send(x.window(hwnd), "WM_CHANGE_STATE", [3])        # IconicState
            _forget()
            return True
        except Exception:
            return False


def maximize(hwnd) -> bool:
    x = _conn()
    with x.lock:
        try:
            x.send(x.window(hwnd), "_NET_WM_STATE", [1, x.atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                                                    x.atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 2])
            _forget()
            return True
        except Exception:
            return False


def focus(hwnd) -> bool:
    """Raise, un-minimize and give it the keyboard."""
    x = _conn()
    with x.lock:
        try:
            x.send(x.window(hwnd), "_NET_ACTIVE_WINDOW", [2, 0, 0])
            _forget()
            return True
        except Exception:
            return False


restore = focus


class HoloSkin(PlainSkin):
    def __init__(self, alpha: int = 255):
        import sys
        super().__init__(sys.modules[__name__], alpha)


# ---------------------------------------------------------------- input and hotkeys
class X11Input(PynputInput):
    title_height = 34.0

    def __init__(self):
        import sys
        super().__init__(sys.modules[__name__])
        x = _conn()
        self.origin = x.origin
        g = x.root.get_geometry()
        self._root = (g.width, g.height)

    def bounds(self) -> tuple:
        ox, oy = self.origin
        return -ox, -oy, self._root[0], self._root[1]

    def zoom(self, amount: float) -> None:
        sx, sy = self._wheel.add(0, amount)
        if sy:
            with self._keys.pressed(self._Key.ctrl):
                self._mouse.scroll(0, sy)


def make_input():
    return X11Input()


_codes: dict = {}


def key_is_down(keymap, code: int) -> bool:
    """keymap = the 32 bytes of XQueryKeymap: one bit per key code."""
    return bool(code) and bool(keymap[code // 8] & (1 << (code % 8)))


def start_hotkeys() -> bool:
    """Polled like on Windows (XQueryKeymap): no listener thread."""
    from Xlib import XK
    x = _conn()
    for name in ("Control_L", "Control_R", "Alt_L", "Alt_R", "a", "c", "g", "h", "j", "m", "q", "s", "x"):
        _codes[name] = x.d.keysym_to_keycode(XK.string_to_keysym(name))
    return True


def hotkey_down(letter: str) -> bool:
    """Ctrl+Alt+<letter>."""
    if not _codes:
        return False
    x = _conn()
    with x.lock:
        try:
            keys = x.d.query_keymap()
        except Exception:
            return False
    return ((key_is_down(keys, _codes["Control_L"]) or key_is_down(keys, _codes["Control_R"]))
            and (key_is_down(keys, _codes["Alt_L"]) or key_is_down(keys, _codes["Alt_R"]))
            and key_is_down(keys, _codes[letter]))


# ---------------------------------------------------------------- files and apps
APP_DIRS = ("~/.local/share/applications", "/usr/local/share/applications", "/usr/share/applications",
            "/var/lib/flatpak/exports/share/applications", "~/.local/share/flatpak/exports/share/applications",
            "/var/lib/snapd/desktop/applications")


def open_path(path: str) -> None:
    if path.endswith(".desktop"):
        if run_quiet("which", "gtk-launch").strip():
            open_detached("gtk-launch", Path(path).stem)
        else:
            open_detached("gio", "launch", path)
    else:
        open_detached("xdg-open", path)


def is_hidden(path: Path) -> bool:
    return path.name.startswith(".")


def power(action: str) -> bool:
    cmd = {"lock": ["loginctl", "lock-session"], "sleep": ["systemctl", "suspend"],
           "restart": ["systemctl", "reboot"], "shutdown": ["systemctl", "poweroff"],
           "signout": ["loginctl", "terminate-user", os.environ.get("USER", "")]}.get(action)
    if cmd is None:
        return False
    open_detached(*cmd)
    return True


def type_text(text: str) -> None:
    from .common import pynput_type
    pynput_type(text)


def close_window(hwnd) -> bool:
    x = _conn()
    with x.lock:
        try:
            x.send(x.window(hwnd), "_NET_CLOSE_WINDOW", [0, 2])
            _forget()
            return True
        except Exception:
            return False


def app_list() -> list:
    seen, out = set(), []
    for folder in APP_DIRS:
        base = Path(os.path.expanduser(folder))
        if not base.is_dir():
            continue
        for p in sorted(base.glob("*.desktop")):
            if p.name in seen:
                continue
            seen.add(p.name)
            try:
                entry = parse_desktop_entry(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if entry:
                out.append((entry[0], str(p)))
    out.sort(key=lambda i: i[0].lower())
    return out
