"""macOS backend: windows through Quartz (listing) and the Accessibility API (moving, minimizing),
mouse and keys through Quartz events and pynput.

macOS asks for permissions the first time; grant them to the app that runs Python (Terminal,
iTerm, VS Code...) in System Settings > Privacy & Security:
    Camera            to see the hands
    Accessibility     to move the mouse, click and move windows
    Screen Recording  optional: lets the window list show window titles, not only app names
Not available on macOS: see-through windows of other programs and live window previews.
Coordinates are points (not pixels) from the top-left of the main display.
"""
import os
import time
from pathlib import Path

from .common import NoThumbnail as Thumbnail  # noqa: F401
from .common import (PlainSkin, PynputInput, WheelAccumulator, WinInfo, attach_to_desktop, hit,  # noqa: F401
                     make_dpi_aware, open_detached, refresh_wallpaper)

NAME = "mac"
FEATURES = {"translucent": False, "thumbnails": False, "wallpaper": False}
CACHE = 0.15
# Virtual key codes (US layout positions) for the Ctrl+Option hotkeys, and the modifier keys
KEYCODES = {"a": 0, "c": 8, "g": 5, "j": 38, "h": 4, "m": 46, "q": 12, "s": 1, "x": 7}
CTRL_KEYS, OPTION_KEYS = (59, 62), (58, 61)


def _Q():
    import Quartz
    return Quartz


def _AS():
    import ApplicationServices
    return ApplicationServices


# ---------------------------------------------------------------- pure matching (tested on any OS)
def match_windows(entries: list, ax_windows: list, tolerance: float = 2.0) -> dict:
    """Pair Quartz window entries with Accessibility windows of the same app.

    entries: (number, (l, t, r, b), title, onscreen); ax_windows: (key, (l, t, r, b), title, minimized).
    A window on screen pairs by its frame; a minimized one (Quartz no longer knows where it is) by title.
    Returns number -> key."""
    used, out = set(), {}
    for num, rect, title, onscreen in entries:
        for key, arect, atitle, minimized in ax_windows:
            if key in used:
                continue
            if onscreen and not minimized and all(abs(a - b) <= tolerance for a, b in zip(rect, arect)):
                break
            if not onscreen and minimized and title and title == atitle:
                break
        else:
            continue
        used.add(key)
        out[num] = key
    return out


# ---------------------------------------------------------------- Accessibility helpers
def _attr(el, name):
    AS = _AS()
    err, value = AS.AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def _rect_of(el):
    AS = _AS()
    pos, size = _attr(el, "AXPosition"), _attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok2, s = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return p.x, p.y, p.x + s.width, p.y + s.height


def _set_rect(el, rect) -> bool:
    AS, Q = _AS(), _Q()
    l, t, r, b = rect
    pos = AS.AXValueCreate(AS.kAXValueCGPointType, Q.CGPointMake(l, t))
    size = AS.AXValueCreate(AS.kAXValueCGSizeType, Q.CGSizeMake(max(1, r - l), max(1, b - t)))
    ok = AS.AXUIElementSetAttributeValue(el, "AXPosition", pos) == 0
    ok = AS.AXUIElementSetAttributeValue(el, "AXSize", size) == 0 and ok
    AS.AXUIElementSetAttributeValue(el, "AXPosition", pos)   # again: a size change can push it off its corner
    return ok


_trusted = None


def trusted(prompt: bool = False) -> bool:
    """Has the user allowed this program to control the computer (Accessibility)?"""
    global _trusted
    if _trusted and not prompt:
        return True
    try:
        AS = _AS()
        _trusted = bool(AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        _trusted = False
    return _trusted


# ---------------------------------------------------------------- screen and windows
def screen_size() -> tuple:
    Q = _Q()
    b = Q.CGDisplayBounds(Q.CGMainDisplayID())
    return int(b.size.width), int(b.size.height)


def _visible_frame() -> tuple:
    """The main screen without the menu bar and the Dock, top-left origin."""
    try:
        from AppKit import NSScreen
        screen = NSScreen.screens()[0]
        full, vis = screen.frame(), screen.visibleFrame()
        top = full.size.height - (vis.origin.y + vis.size.height)
        return vis.origin.x, top, vis.origin.x + vis.size.width, top + vis.size.height
    except Exception:
        w, h = screen_size()
        return 0, 25, w, h


_cache = (0.0, [])
_ax: dict = {}          # window number -> AX window element
_pid: dict = {}         # window number -> owning process


def list_windows(exclude_pid=None) -> list:
    global _cache
    now = time.monotonic()
    if now - _cache[0] < CACHE:
        return list(_cache[1])
    Q, AS = _Q(), _AS()
    own = os.getpid() if exclude_pid is None else exclude_pid
    infos = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll | Q.kCGWindowListExcludeDesktopElements,
                                         Q.kCGNullWindowID) or []
    entries, by_pid = [], {}
    for w in infos:                                   # front to back
        if w.get("kCGWindowLayer", 1) != 0:
            continue
        pid = int(w.get("kCGWindowOwnerPID", 0))
        if pid == own:
            continue
        b = w.get("kCGWindowBounds") or {}
        l, t = float(b.get("X", 0)), float(b.get("Y", 0))
        rect = (l, t, l + float(b.get("Width", 0)), t + float(b.get("Height", 0)))
        if rect[2] - rect[0] < 40 or rect[3] - rect[1] < 30:
            continue
        num = int(w["kCGWindowNumber"])
        entry = (num, rect, str(w.get("kCGWindowName") or ""), bool(w.get("kCGWindowIsOnscreen", False)))
        entries.append((entry, pid, str(w.get("kCGWindowOwnerName") or "")))
        by_pid.setdefault(pid, []).append(entry)

    ax_ok = trusted()
    found: dict = {}                                   # number -> (element, title, minimized, rect)
    if ax_ok:
        for pid, mine in by_pid.items():
            app = AS.AXUIElementCreateApplication(pid)
            axw = []
            for i, el in enumerate(_attr(app, "AXWindows") or []):
                if _attr(el, "AXSubrole") not in (None, "AXStandardWindow", "AXDialog"):
                    continue
                minimized = bool(_attr(el, "AXMinimized"))
                axw.append((i, _rect_of(el) or (0, 0, 0, 0), str(_attr(el, "AXTitle") or ""), minimized, el))
            pairs = match_windows(mine, [a[:4] for a in axw])
            for num, i in pairs.items():
                a = next(a for a in axw if a[0] == i)
                found[num] = (a[4], a[2], a[3], a[1])

    vis = _visible_frame()
    sw, sh = screen_size()
    out = []
    for (num, rect, cg_title, onscreen), pid, owner in entries:
        if num in found:
            el, title, minimized, arect = found[num]
            _ax[num], _pid[num] = el, pid
        elif onscreen and not ax_ok:
            title, minimized = cg_title, False       # no Accessibility: list it, it just cannot be moved
            _pid[num] = pid
        else:
            continue                                 # helper / off-space windows with no real window behind
        name = title or cg_title or owner
        if title and owner and owner not in title:
            name = f"{owner} — {title}"
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if not minimized and not (0 <= cx < sw and 0 <= cy < sh):
            continue
        maximized = all(abs(a - b) <= 4 for a, b in zip(rect, vis))
        out.append(WinInfo(num, name, tuple(int(v) for v in rect), (0, 0, int(rect[2] - rect[0]), int(rect[3] - rect[1])),
                           minimized, maximized))
    _cache = (now, out)
    return list(out)


def _forget() -> None:
    global _cache
    _cache = (0.0, [])


def window_at(x, y):
    return hit(list_windows(), x, y)


def frame_bounds(hwnd):
    return next((w.rect for w in list_windows() if w.hwnd == hwnd), None)


def _element(hwnd):
    if hwnd not in _ax:
        _forget()
        list_windows()
    return _ax.get(hwnd)


def place(hwnd, rect) -> bool:
    el = _element(hwnd)
    if el is None:
        return False
    AS = _AS()
    if _attr(el, "AXMinimized"):
        AS.AXUIElementSetAttributeValue(el, "AXMinimized", False)
    ok = _set_rect(el, rect)
    _forget()
    return ok


def minimize(hwnd) -> bool:
    el = _element(hwnd)
    if el is None:
        return False
    ok = _AS().AXUIElementSetAttributeValue(el, "AXMinimized", True) == 0
    _forget()
    return ok


def maximize(hwnd) -> bool:
    return place(hwnd, _visible_frame())     # the green button's "fill", without full-screen spaces


def focus(hwnd) -> bool:
    el = _element(hwnd)
    pid = _pid.get(hwnd)
    if pid is None:
        return False
    try:
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        pass
    if el is not None:
        AS = _AS()
        if _attr(el, "AXMinimized"):
            AS.AXUIElementSetAttributeValue(el, "AXMinimized", False)
        AS.AXUIElementPerformAction(el, "AXRaise")
    _forget()
    return True


restore = focus


class HoloSkin(PlainSkin):
    def __init__(self, alpha: int = 255):
        import sys
        super().__init__(sys.modules[__name__], alpha)


# ---------------------------------------------------------------- input and hotkeys
class MacInput(PynputInput):
    title_height = 28.0

    def __init__(self):
        import sys
        if not trusted(prompt=True):
            print("macOS: allow this terminal in System Settings > Privacy & Security > Accessibility, "
                  "then start sensor-OS again. Until then the mouse and windows cannot be moved.", flush=True)
        super().__init__(sys.modules[__name__])
        self._lines = WheelAccumulator(per_step=40.0)      # one Windows notch (120) = 3 lines
        self._zoom = WheelAccumulator(per_step=120.0)

    def bounds(self) -> tuple:
        Q = _Q()
        try:
            err, ids, n = Q.CGGetActiveDisplayList(16, None, None)
            rects = [Q.CGDisplayBounds(i) for i in ids[:n]]
            l = min(r.origin.x for r in rects)
            t = min(r.origin.y for r in rects)
            r_ = max(r.origin.x + r.size.width for r in rects)
            b = max(r.origin.y + r.size.height for r in rects)
            return int(l), int(t), int(r_ - l), int(b - t)
        except Exception:
            return super().bounds()

    def wheel(self, dx: float, dy: float) -> None:
        sx, sy = self._lines.add(dx, dy)
        if sx or sy:
            Q = _Q()
            ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 2, sy, sx)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)

    def zoom(self, amount: float) -> None:
        _, steps = self._zoom.add(0, amount)
        key = "=" if steps > 0 else "-"
        for _ in range(abs(steps)):                         # Cmd + / Cmd - : zoom in every Mac app that zooms
            with self._keys.pressed(self._Key.cmd):
                self._keys.press(key)
                self._keys.release(key)


def make_input():
    return MacInput()


def _down(code: int) -> bool:
    Q = _Q()
    return bool(Q.CGEventSourceKeyState(Q.kCGEventSourceStateCombinedSessionState, code))


def start_hotkeys() -> bool:
    return True                 # polled like on Windows: no listener thread, no extra permission


def hotkey_down(letter: str) -> bool:
    """Ctrl+Option+<letter>."""
    try:
        return any(_down(c) for c in CTRL_KEYS) and any(_down(c) for c in OPTION_KEYS) and _down(KEYCODES[letter])
    except Exception:
        return False


# ---------------------------------------------------------------- files and apps
APP_DIRS = ("/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", "~/Applications")


def open_path(path: str) -> None:
    open_detached("open", path)       # the same as double-clicking it in Finder


def is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        return bool(path.stat().st_flags & 0x8000)           # UF_HIDDEN
    except (OSError, AttributeError):
        return False


def power(action: str) -> bool:
    script = {"restart": 'tell application "System Events" to restart',
              "shutdown": 'tell application "System Events" to shut down',
              "signout": 'tell application "System Events" to log out'}.get(action)
    if script:
        open_detached("osascript", "-e", script)
    elif action == "lock":
        open_detached("pmset", "displaysleepnow")
    elif action == "sleep":
        open_detached("pmset", "sleepnow")
    else:
        return False
    return True


def type_text(text: str) -> None:
    from .common import pynput_type
    pynput_type(text)


def close_window(hwnd) -> bool:
    el = _element(hwnd)
    if el is None:
        return False
    button = _attr(el, "AXCloseButton")
    return button is not None and _AS().AXUIElementPerformAction(button, "AXPress") == 0


def app_list() -> list:
    seen, out = set(), []
    for folder in APP_DIRS:
        base = Path(os.path.expanduser(folder))
        if not base.is_dir():
            continue
        for p in base.glob("*.app"):
            if p.stem.lower() not in seen:
                seen.add(p.stem.lower())
                out.append((p.stem, str(p)))
    out.sort(key=lambda i: i[0].lower())
    return out
