"""Windows backend: user32 / dwmapi through ctypes. Everything the hologram can do, it can do here."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .. import windows as _w
from ..windows import (HoloSkin, Thumbnail, WinInfo, attach_to_desktop, focus, frame_bounds, list_windows,  # noqa: F401
                       maximize, minimize, place, refresh_wallpaper, restore, screen_size, window_at)

NAME = "windows"
FEATURES = {"translucent": True, "thumbnails": True, "wallpaper": True}
user32 = _w.user32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
VK_CONTROL, VK_MENU, VK_LWIN = 0x11, 0x12, 0x5B
VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN = 0x25, 0x26, 0x27, 0x28
VK_MEDIA = {"next": 0xB0, "prev": 0xB1, "play": 0xB3}
HOTKEYS = {"a": 0x41, "c": 0x43, "g": 0x47, "j": 0x4A, "h": 0x48, "m": 0x4D, "q": 0x51, "s": 0x53, "x": 0x58}
WM_NCHITTEST, HTCAPTION, GA_ROOT, SMTO_ABORTIFHUNG = 0x0084, 2, 2, 0x0002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


def make_dpi_aware() -> None:
    """Work in real pixels. Harmless if Qt already set this."""
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # per-monitor
    except (OSError, AttributeError):
        user32.SetProcessDPIAware()


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags: int, data: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
    return i


def _key(vk: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in (VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_LWIN):
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return i


def _key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def hotkey_down(letter: str) -> bool:
    """Ctrl+Alt+<letter>."""
    return _key_down(VK_CONTROL) and _key_down(VK_MENU) and _key_down(HOTKEYS[letter])


def start_hotkeys() -> bool:
    return True                 # polled directly with GetAsyncKeyState


class WinInput:
    title_height = 0            # not used: Windows answers "is this the title bar" itself

    def screen(self) -> tuple:
        return screen_size()

    def bounds(self) -> tuple:
        return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))

    def cursor(self) -> tuple:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def set_cursor(self, x: int, y: int) -> None:
        # SetCursorPos is pixel-exact; SendInput's 0..65535 absolute scale lands 1 px off
        # about one time in five, which matters when aiming at small targets.
        user32.SetCursorPos(int(x), int(y))

    def button(self, name: str, down: bool) -> None:
        if name == "left":
            _send(_mouse(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP))
        else:
            _send(_mouse(MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP))

    def click(self, name: str) -> None:
        if name == "left":
            _send(_mouse(MOUSEEVENTF_LEFTDOWN), _mouse(MOUSEEVENTF_LEFTUP))
        else:
            _send(_mouse(MOUSEEVENTF_RIGHTDOWN), _mouse(MOUSEEVENTF_RIGHTUP))

    def wheel(self, dx: float, dy: float) -> None:
        if dy:
            _send(_mouse(MOUSEEVENTF_WHEEL, int(dy)))
        if dx:
            _send(_mouse(MOUSEEVENTF_HWHEEL, int(dx)))

    def zoom(self, amount: float) -> None:
        _send(_key(VK_CONTROL), _mouse(MOUSEEVENTF_WHEEL, int(amount)), _key(VK_CONTROL, True))

    def on_title_bar(self) -> bool:
        """True if the cursor is over the title bar of another program's window."""
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return False
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        result = ctypes.c_size_t()
        lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
        ok = user32.SendMessageTimeoutW(root, WM_NCHITTEST, 0, lparam, SMTO_ABORTIFHUNG, 100, ctypes.byref(result))
        return bool(ok) and result.value == HTCAPTION

    def snap(self, direction: str) -> bool:
        """Win + arrow: sideways = snap to that half, up = maximize, down = minimize."""
        arrow = {"left": VK_LEFT, "right": VK_RIGHT, "up": VK_UP, "down": VK_DOWN}[direction]
        _send(_key(VK_LWIN), _key(arrow), _key(arrow, True), _key(VK_LWIN, True))
        return True

    def media(self, action: str) -> None:
        vk = VK_MEDIA.get(action)
        if vk is not None:
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def make_input():
    return WinInput()


# ---------------------------------------------------------------- files and apps
SKIP_APPS = ("uninstall", "readme", "help", "license", "website", "documentation", "release notes")


def open_path(path: str) -> None:
    os.startfile(path)      # the same as double-clicking it in Explorer


def is_hidden(path: Path) -> bool:
    try:
        return bool(path.stat().st_file_attributes & 0x2)       # FILE_ATTRIBUTE_HIDDEN
    except (OSError, AttributeError):
        return True


KEYEVENTF_UNICODE = 0x0004
WM_CLOSE = 0x0010


def type_text(text: str) -> None:
    """Type text into the window in front, any language (Unicode key events, not the keyboard layout)."""
    raw = text.replace("\n", "\r").encode("utf-16-le")
    codes = [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]   # UTF-16 units, as Windows wants
    events = []
    for code in codes:
        for up in (False, True):
            i = INPUT(type=INPUT_KEYBOARD)
            i.ki = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
            events.append(i)
    for k in range(0, len(events), 64):                 # in chunks: some apps drop very long bursts
        _send(*events[k:k + 64])


def close_window(hwnd) -> bool:
    """Ask a window to close, as its X button does (the program may still ask to save first)."""
    return bool(user32.PostMessageW(hwnd, WM_CLOSE, 0, 0))


def power(action: str) -> bool:
    """lock / sleep / restart / shutdown / signout."""
    import subprocess
    if action == "lock":
        return bool(user32.LockWorkStation())
    cmd = {"sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
           "restart": ["shutdown", "/r", "/t", "0"], "shutdown": ["shutdown", "/s", "/t", "0"],
           "signout": ["shutdown", "/l"]}.get(action)
    if cmd is None:
        return False
    subprocess.Popen(cmd, creationflags=0x08000000)       # no console window
    return True


def app_list() -> list:
    """(label, path): pinned taskbar apps first, then the Start menu A-Z."""
    appdata = Path(os.environ.get("APPDATA", ""))
    programdata = Path(os.environ.get("PROGRAMDATA", ""))
    pinned = appdata / "Microsoft/Internet Explorer/Quick Launch/User Pinned/TaskBar"
    menus = (appdata / "Microsoft/Windows/Start Menu/Programs", programdata / "Microsoft/Windows/Start Menu/Programs")
    seen, first, rest = set(), [], []
    for folder, bucket, pattern in ((pinned, first, "*.lnk"), (menus[0], rest, "**/*.lnk"), (menus[1], rest, "**/*.lnk")):
        if not folder.is_dir():
            continue
        for p in folder.glob(pattern):
            key = p.stem.lower()
            if key in seen or any(word in key for word in SKIP_APPS):
                continue
            seen.add(key)
            bucket.append((p.stem, str(p)))
    rest.sort(key=lambda i: i[0].lower())
    return first + rest
