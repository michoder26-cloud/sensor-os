"""What the ring shows: the real windows, folders and apps of this computer."""
import os
from pathlib import Path

from . import desktop, models3d
from .hub import Item
from .i18n import tr

FOLDERS = ((tr("Desktop"), "Desktop"), (tr("Documents"), "Documents"), (tr("Downloads"), "Downloads"),
           (tr("Pictures"), "Pictures"), (tr("Videos"), "Videos"), (tr("Music"), "Music"))
MAX_ROWS = 300


def _hidden(path: Path) -> bool:
    name = path.name
    if name.startswith((".", "~$")) or name.lower() in ("desktop.ini", "thumbs.db"):
        return True
    return desktop.is_hidden(path)


def roots() -> list[Item]:
    home = Path.home()
    out = []
    for label, name in FOLDERS:
        for base in (home, home / "OneDrive"):
            p = base / name
            if p.is_dir():
                out.append(Item(label, "dir", str(p)))
                break
    return out


def listing(folder: str) -> list[Item]:
    entries = [p for p in Path(folder).iterdir() if not _hidden(p)]
    def folder(p):
        return p.is_dir() and p.suffix.lower() != ".app"      # a macOS app is a folder you open, not browse

    entries.sort(key=lambda p: (not folder(p), p.name.lower()))

    def kind(p):
        return "dir" if folder(p) else "model" if p.suffix.lower() in models3d.EXTENSIONS else "file"

    return [Item(("▸  " if folder(p) else "◈  " if kind(p) == "model" else "")
                 + (p.stem if p.suffix.lower() == ".lnk" else p.name), kind(p), str(p)) for p in entries[:MAX_ROWS]]


def _can_open(p: Path) -> bool:
    """A folder Windows lets us look into (Boot, PerfLogs, other users' folders... do not)."""
    if not p.is_dir():
        return True
    try:
        with os.scandir(p) as it:
            next(it, None)
        return True
    except OSError:
        return False


def entries(folder: str) -> list:
    """(name, path, is_dir) of a folder's visible contents, folders first. Folders that cannot be
    opened are left out, so every tile in a file panel does something."""
    try:
        found = [p for p in Path(folder).iterdir() if not _hidden(p) and _can_open(p)]
    except OSError:
        return []
    found.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    return [(p.stem if p.suffix.lower() == ".lnk" else p.name, str(p), p.is_dir()) for p in found[:MAX_ROWS]]


def folder_of(name: str):
    """Path of one of the user's standard folders (Desktop, Pictures...), or None."""
    home = Path.home()
    for base in (home, home / "OneDrive"):
        p = base / name
        if p.is_dir():
            return str(p)
    return None


def space_provider(kind: str, arg):
    """Files for the glass space panels."""
    if kind == "folder":
        return entries(arg)
    if kind == "images":
        return [path for _, path, is_dir in entries(arg)
                if not is_dir and path.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))]
    if kind == "models":
        return [(label, ref) for label, ref in models3d.available() if not ref.startswith("control:")]
    if kind == "home":
        return folder_of("Desktop") or str(Path.home())
    if kind == "apps":
        return desktop.app_list()[:MAX_ROWS]
    if kind == "windows":
        return [(w.title, w.hwnd, w.minimized) for w in desktop.list_windows()]
    if kind == "pictures":
        return folder_of("Pictures")
    return []


def apps() -> list[Item]:
    return [Item(label, "app", path) for label, path in desktop.app_list()[:MAX_ROWS]]


def provide(kind: str, arg):
    if kind == "windows":
        return [Item(("–  " if w.minimized else "") + w.title, "window", w.hwnd) for w in desktop.list_windows()]
    if kind == "files":
        return roots() if arg is None else listing(arg)
    if kind == "apps":
        return apps()
    if kind == "models":
        return [Item(label, "model", ref) for label, ref in models3d.available()]
    return []


def open_path(path: str) -> None:
    desktop.open_path(path)     # the same as double-clicking it in Explorer / Finder / the file manager
