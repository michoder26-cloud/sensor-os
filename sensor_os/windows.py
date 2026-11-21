"""Real Windows windows: list them, move / resize / minimize / focus them, and show live thumbnails.

Everything here is plain user32 / dwmapi through ctypes. Rectangles are (left, top, right, bottom)
in physical screen pixels and describe the *visible* frame of a window (without the invisible
resize borders Windows 10/11 adds around it).
"""
import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
GW_OWNER = 4
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SKIP_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}

DWM_TNP_RECTDESTINATION = 0x01
DWM_TNP_RECTSOURCE = 0x02
DWM_TNP_OPACITY = 0x04
DWM_TNP_VISIBLE = 0x08
DWM_TNP_SOURCECLIENTAREAONLY = 0x10

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wintypes.DWORD), ("rcDestination", wintypes.RECT), ("rcSource", wintypes.RECT),
                ("opacity", ctypes.c_ubyte), ("fVisible", wintypes.BOOL), ("fSourceClientAreaOnly", wintypes.BOOL)]


user32.EnumWindows.argtypes = (EnumWindowsProc, wintypes.LPARAM)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsZoomed.argtypes = (wintypes.HWND,)
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowPlacement.argtypes = (wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT))
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT)
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.keybd_event.argtypes = (ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t)
dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
dwmapi.DwmRegisterThumbnail.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.POINTER(wintypes.HANDLE))
dwmapi.DwmUnregisterThumbnail.argtypes = (wintypes.HANDLE,)
dwmapi.DwmUpdateThumbnailProperties.argtypes = (wintypes.HANDLE, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES))


@dataclass
class WinInfo:
    hwnd: int
    title: str
    rect: tuple          # visible frame (l, t, r, b); for a minimized window, where it will come back
    source: tuple        # visible frame inside the full window rect, for thumbnails
    minimized: bool
    maximized: bool


def screen_size() -> tuple:
    """Primary screen in physical pixels (the process must be DPI aware; Qt and the controller make it so)."""
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def _rect(r) -> tuple:
    return r.left, r.top, r.right, r.bottom


def frame_bounds(hwnd) -> tuple | None:
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        return None
    return _rect(r)


def window_rect(hwnd) -> tuple:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _rect(r)


def _cloaked(hwnd) -> bool:
    v = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return v.value != 0


def _text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def list_windows(exclude_pid: int | None = None) -> list[WinInfo]:
    """Normal application windows on the primary screen, front-most first."""
    own = os.getpid() if exclude_pid is None else exclude_pid
    sw, sh = screen_size()
    found: list[WinInfo] = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or _cloaked(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if (ex & WS_EX_TOOLWINDOW or ex & WS_EX_NOACTIVATE) and not ex & WS_EX_APPWINDOW:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own:
            return True
        title = _text(hwnd)
        if not title or _class(hwnd) in SKIP_CLASSES:
            return True
        minimized = bool(user32.IsIconic(hwnd))
        wr = window_rect(hwnd)
        fb = frame_bounds(hwnd) or wr
        if minimized:
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(wp)
            user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
            rect = _rect(wp.rcNormalPosition)
            source = (0, 0, rect[2] - rect[0], rect[3] - rect[1])
        else:
            rect = fb
            source = (fb[0] - wr[0], fb[1] - wr[1], fb[2] - wr[0], fb[3] - wr[1])
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < 40 or h < 30:
            return True
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        if not minimized and not (0 <= cx < sw and 0 <= cy < sh):
            return True      # lives on another monitor
        found.append(WinInfo(int(hwnd), title, rect, source, minimized, bool(user32.IsZoomed(hwnd))))
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return found


def place(hwnd, rect) -> bool:
    """Move / resize so the visible frame lands exactly on rect."""
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    wr = window_rect(hwnd)
    fb = frame_bounds(hwnd) or wr
    dl, dt, dr, db = fb[0] - wr[0], fb[1] - wr[1], wr[2] - fb[2], wr[3] - fb[3]
    l, t, r, b = (int(round(v)) for v in rect)
    return bool(user32.SetWindowPos(hwnd, None, l - dl, t - dt, (r - l) + dl + dr, (b - t) + dt + db,
                                    SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS))


def minimize(hwnd) -> bool:
    return bool(user32.ShowWindowAsync(hwnd, SW_MINIMIZE))


def maximize(hwnd) -> bool:
    return bool(user32.ShowWindowAsync(hwnd, SW_MAXIMIZE))


def restore(hwnd) -> bool:
    return bool(user32.ShowWindowAsync(hwnd, SW_RESTORE))


def focus(hwnd) -> bool:
    """Bring a window to the front and give it the keyboard."""
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground process hand focus away. A synthetic Alt tap
    # counts as user input, which lifts that lock for the call that follows.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    ok = bool(user32.SetForegroundWindow(hwnd))
    time.sleep(0.02)
    return ok or user32.GetForegroundWindow() == hwnd


class Thumbnail:
    """A live DWM thumbnail of `source` drawn inside `dest` (Windows does the drawing)."""

    def __init__(self, dest_hwnd: int, source_hwnd: int):
        self.handle = wintypes.HANDLE()
        self.ok = dwmapi.DwmRegisterThumbnail(dest_hwnd, source_hwnd, ctypes.byref(self.handle)) == 0

    def show(self, dest: tuple, source: tuple | None = None, opacity: int = 255) -> None:
        if not self.ok:
            return
        p = DWM_THUMBNAIL_PROPERTIES()
        p.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_OPACITY | DWM_TNP_VISIBLE | DWM_TNP_SOURCECLIENTAREAONLY
        p.rcDestination = wintypes.RECT(*(int(round(v)) for v in dest))
        if source is not None:
            p.dwFlags |= DWM_TNP_RECTSOURCE
            p.rcSource = wintypes.RECT(*(int(v) for v in source))
        p.opacity = max(0, min(255, int(opacity)))
        p.fVisible = True
        p.fSourceClientAreaOnly = False
        dwmapi.DwmUpdateThumbnailProperties(self.handle, ctypes.byref(p))

    def close(self) -> None:
        if self.ok:
            dwmapi.DwmUnregisterThumbnail(self.handle)
            self.ok = False


# =============================================================================
# Hologram support: camera as live wallpaper, translucent windows, window under a point
# =============================================================================
import json
from pathlib import Path

GWL_STYLE = -16
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x2
GA_ROOT = 2
SW_SHOWNA = 8
SWP_SHOWWINDOW = 0x0040
RDW_REDRAW = 0x0001 | 0x0004 | 0x0080 | 0x0400     # invalidate | erase | all children | frame
SKIN_STATE = Path(__file__).resolve().parent.parent / "holo_state.json"

_get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_get_long.argtypes = (wintypes.HWND, ctypes.c_int)
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
_set_long.restype = ctypes.c_ssize_t
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
user32.FindWindowExW.restype = wintypes.HWND
user32.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.SetParent.argtypes = (wintypes.HWND, wintypes.HWND)
user32.SetParent.restype = wintypes.HWND
user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD)
user32.RedrawWindow.argtypes = (wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND


def desktop_host() -> int | None:
    """The WorkerW that sits behind the desktop icons (where live wallpapers go)."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None
    res = ctypes.c_size_t()
    # Undocumented but long-stable: asks Progman to split the wallpaper into its own WorkerW.
    user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    hosts = []

    def visit(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            host = user32.FindWindowExW(None, hwnd, "WorkerW", None)
            if host:
                hosts.append(host)
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return int(hosts[0]) if hosts else None


def attach_to_desktop(hwnd: int) -> bool:
    """Put our window behind the desktop icons, covering the wallpaper."""
    host = desktop_host()
    if not host:
        return False
    user32.SetParent(hwnd, host)
    sw, sh = screen_size()
    user32.SetWindowPos(hwnd, None, 0, 0, sw, sh, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    return True


def window_at(x: int, y: int) -> int | None:
    """Top-level application window under a screen point (not ours, not the desktop or taskbar)."""
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return None
    root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    if pid.value == os.getpid() or _class(root) in SKIP_CLASSES:
        return None
    return int(root)


def refresh_wallpaper() -> None:
    """Make Windows paint the real wallpaper again (it keeps our last camera frame otherwise)."""
    user32.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
    buf = ctypes.create_unicode_buffer(520)
    if user32.SystemParametersInfoW(0x0073, 520, buf, 0):          # SPI_GETDESKWALLPAPER
        user32.SystemParametersInfoW(0x0014, 0, buf, 0)             # SPI_SETDESKWALLPAPER, same picture: just redraw
    for hwnd in (user32.FindWindowW("Progman", None), desktop_host()):
        if hwnd:
            user32.RedrawWindow(hwnd, None, None, RDW_REDRAW | 0x0100)   # + update now


def _icons_view():
    """The list view that draws the desktop icons."""
    defview = user32.FindWindowExW(user32.FindWindowW("Progman", None), None, "SHELLDLL_DefView", None)
    if not defview:
        found = []

        def visit(hwnd, _):
            d = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if d:
                found.append(d)
            return True

        user32.EnumWindows(EnumWindowsProc(visit), 0)
        defview = found[0] if found else None
    return user32.FindWindowExW(defview, None, "SysListView32", None) if defview else None


def show_taskbar(show: bool) -> bool:
    """Hide / show the taskbar on every screen (the real one; Windows keeps running underneath)."""
    found = []

    def visit(hwnd, _):
        if _class(hwnd) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    for hwnd in found:
        user32.ShowWindow(hwnd, 5 if show else 0)
    return bool(found)


def show_desktop_icons(show: bool) -> bool:
    view = _icons_view()
    if not view:
        return False
    user32.ShowWindow(view, 5 if show else 0)
    return True


class HoloSkin:
    """Dresses the desktop as a hologram and puts everything back exactly as it was:
    windows slightly see-through, maximized windows shrunk to floating cards, desktop icons hidden."""

    def __init__(self, alpha: int):
        self.alpha = max(40, min(255, int(alpha)))
        self.touched: dict[int, bool] = {}     # hwnd -> True if we made it layered
        self.maxed: list[int] = []             # windows we un-maximized
        self.icons_hidden = False
        self.taskbar_hidden = False
        self.recover()

    def apply(self, infos) -> None:
        if self.alpha >= 255:
            return
        changed = False
        for info in infos:
            hwnd = info.hwnd
            if hwnd in self.touched:
                continue
            ex = _get_long(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_LAYERED:
                self.touched[hwnd] = False       # already layered by its own program: leave it alone
                continue
            _set_long(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            if user32.SetLayeredWindowAttributes(hwnd, 0, self.alpha, LWA_ALPHA):
                self.touched[hwnd] = True
                changed = True
            else:
                _set_long(hwnd, GWL_EXSTYLE, ex)
                self.touched[hwnd] = False
        if changed:
            self._save()

    def cardify(self, infos) -> None:
        """Turn maximized windows into floating cards so the camera image shows around them."""
        sw, sh = screen_size()
        n = 0
        for info in infos:
            if info.minimized or not info.maximized or info.hwnd in self.maxed:
                continue
            self.maxed.append(info.hwnd)
            self._save()
            w, h = int(sw * 0.56), int(sh * 0.64)
            x, y = int(sw * 0.24) + n * 56, int(sh * 0.10) + n * 44
            place(info.hwnd, (x, y, x + w, y + h))
            n += 1

    def hide_icons(self) -> None:
        if not self.icons_hidden and show_desktop_icons(False):
            self.icons_hidden = True
            self._save()

    def hide_taskbar(self) -> None:
        if not self.taskbar_hidden and show_taskbar(False):
            self.taskbar_hidden = True
            self._save()

    def restore(self) -> None:
        for hwnd, ours in self.touched.items():
            if ours:
                _unskin(hwnd)
        for hwnd in self.maxed:
            if user32.IsWindow(hwnd) and not user32.IsIconic(hwnd):
                user32.ShowWindowAsync(hwnd, SW_MAXIMIZE)
        if self.icons_hidden:
            show_desktop_icons(True)
        if self.taskbar_hidden:
            show_taskbar(True)
        refresh_wallpaper()
        self.touched.clear()
        self.maxed = []
        self.icons_hidden = False
        self.taskbar_hidden = False
        try:
            SKIN_STATE.unlink()
        except OSError:
            pass

    def _save(self) -> None:
        try:
            SKIN_STATE.write_text(json.dumps({"alpha": [h for h, ours in self.touched.items() if ours],
                                              "max": self.maxed, "icons": self.icons_hidden,
                                              "taskbar": self.taskbar_hidden}))
        except OSError:
            pass

    @staticmethod
    def recover() -> int:
        """Undo a hologram left behind by a run that was killed. Returns how many things were fixed."""
        try:
            data = json.loads(SKIN_STATE.read_text())
        except (OSError, ValueError):
            return 0
        refresh_wallpaper()
        if isinstance(data, list):
            data = {"alpha": data}
        n = sum(1 for h in data.get("alpha", []) if _unskin(int(h)))
        for h in data.get("max", []):
            if user32.IsWindow(int(h)) and not user32.IsIconic(int(h)):
                user32.ShowWindowAsync(int(h), SW_MAXIMIZE)
                n += 1
        if data.get("icons"):
            show_desktop_icons(True)
        if data.get("taskbar"):                    # a run that was killed must never leave Windows without a taskbar
            show_taskbar(True)
            n += 1
        try:
            SKIN_STATE.unlink()
        except OSError:
            pass
        return n


def _unskin(hwnd: int) -> bool:
    if not user32.IsWindow(hwnd):
        return False
    ex = _get_long(hwnd, GWL_EXSTYLE)
    if not ex & WS_EX_LAYERED:
        return False
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    _set_long(hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
    user32.RedrawWindow(hwnd, None, None, RDW_REDRAW)
    return True
