"""Glass space: glass panels floating at different depths that you simply grab.

One gesture only. A closed hand holds, an open hand lets go. Pinch, fist or claw all count as
closed, and the grip takes hold on the first frame the hand closes: no pose to hold, nothing to learn.

    grab a panel and move           it stays under your hand (a near miss still catches the nearest panel)
    let go while moving             it slides on, slows down, bounces off the screen edges
    fling it sideways or up         it goes to the shelf at the bottom (nothing is closed)
    fling it down off the screen    a panel you made is thrown away (a window only goes to the shelf)
    hand towards / away from camera while holding: pull the panel nearer (bigger) / push it back
    second hand grabs the same panel: stretch or shrink it
    both hands close on empty space and pull apart: draw a new panel, then choose what goes in it
    grab the + tab on the shelf     a new panel, in your hand
    grab and let go at once         use it: open a window, next picture / model, play music, type a note,
                                    open a file; on a folder entry: go into it
    grab a folder entry and pull    the file comes out as its own panel (a picture, a 3D model, a file)

Pure logic (no Qt, no OS calls); lists of files come from a `provide(kind, arg)` function.
update() returns commands:
    ("grab", id) ("drop", id) ("throw", id) ("stash", id) ("delete", id) ("create", id)
    ("open", id) ("open_file", path) ("media", action) ("edit", id or None) ("next", id)
The hand point is the middle of the palm, not the fingertips, so it stays put however the fingers
close. Its position is filtered, then pushed ahead along its velocity to hide the camera's delay.
"""
import math
import os
from dataclasses import dataclass, field

from .config import Config
from .filters import OneEuroFilter
from .gestures import Hand, pinch_features
from .stage import CameraMap, HandSlot, StageEngine

PALM = (0, 5, 9, 13, 17)
CLOCK, SYSTEM, NEW = -1, -2, -9
SHELF_Y = 0.905          # centre line of the shelf tabs: clear of the taskbar
SHELF_TAB = (0.105, 0.05)
SHELF_GAP = 0.008
CHOICES = ("apps", "windows", "folder", "note", "image", "model", "music", "clock", "system", "power", "ai")
MADE = ("note", "image", "model", "music", "folder", "file", "picker", "clock", "system",
        "apps", "windows", "power", "ai")             # kinds you can make / throw in the bin
APPS_PAGE = 12                                        # 4 x 3 apps per page (the last tile turns the page)
POWER = (("lock", "⊠"), ("sleep", "☾"), ("restart", "↻"), ("shutdown", "⏻"), ("signout", "⇥"), ("exit", "✕"))
CONFIRM = ("lock", "sleep", "restart", "shutdown", "signout", "exit")   # all need a second tap within CONFIRM_TIME
CONFIRM_TIME = 4.0
OFF_SCREEN = 0.6   # a carried panel showing less than this much of itself is thrown away when let go
KIND_WORDS = {"apps": "apps", "แอป": "apps", "windows": "windows", "หน้าต่าง": "windows", "files": "folder",
              "folder": "folder", "ไฟล์": "folder", "โฟลเดอร์": "folder", "clock": "clock", "เวลา": "clock",
              "นาฬิกา": "clock", "system": "system", "ระบบ": "system", "music": "music", "เพลง": "music",
              "picture": "image", "image": "image", "รูป": "image", "note": "note", "โน้ต": "note", "power": "power",
              "พลังงาน": "power", "ai": "ai", "สมอง": "ai", "เลือก ai": "ai"}
MODEL_WORDS = ("3d", "3 d", "model", "โมเดล", "สามมิติ", "3 มิติ", "3มิติ")


def put_away_target(words: str) -> str:
    """What JARVIS was asked to put away: "model" (the floating 3D models), a panel kind, or title words."""
    w = words.strip().lower()
    if any(m in w for m in MODEL_WORDS):
        return "model"
    for word, kind in sorted(KIND_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word in w:
            return kind
    return w


SCENE_NAMES = {"apps": "apps", "windows": "open windows", "folder": "folder", "file": "file", "clock": "clock",
               "system": "system status", "music": "music", "image": "picture", "note": "note", "power": "power",
               "ai": "JARVIS's AI", "model": "3D model", "picker": "new panel (nothing chosen yet)", "window": "program window"}


def describe_scene(space, props=None) -> str:
    """What is on the screen now, for JARVIS (it cannot see it)."""
    panels = list(space.panels)
    out = []
    if space.hidden:
        out.append("Glass panels: all hidden (show_panels brings them back)")
    else:
        shown = [SCENE_NAMES.get(p.kind, p.kind) + (f" '{p.title[:30]}'" if p.title else "")
                 for p in panels if not p.stashed and p.kind != "window"]
        out.append("Glass panels on the screen: " + (", ".join(shown) if shown else "none"))
    shelf = [SCENE_NAMES.get(p.kind, p.kind) for p in panels if p.stashed and p.kind != "window"]
    if shelf:
        out.append("On the shelf at the bottom: " + ", ".join(shelf))
    floating = props.describe() if props is not None else []
    if floating:
        out.append("Floating on the screen: " + ", ".join(floating))
    return "\n".join(out)


EDGE = 0.005       # the pointer this close to the screen's edge is pushing against it
EDGE_SLIDE = 0.25  # seconds: how fast a panel pushed at the edge slides on out of the hand
# Where each kind lands when all panels are called up at once: (x, y, w, h), centre and size
SUMMON_SLOTS = {"clock": (0.13, 0.15, 0.17, 0.15), "system": (0.87, 0.15, 0.17, 0.15),
                "apps": (0.15, 0.55, 0.24, 0.40), "windows": (0.85, 0.47, 0.24, 0.30),
                "folder": (0.50, 0.24, 0.26, 0.26), "power": (0.87, 0.79, 0.16, 0.16)}
# JARVIS's AI: (jarvis_brain, jarvis_model, label)
AI_CHOICES = (("claude_code", "", "Claude"), ("claude_code", "haiku", "Claude Haiku"), ("claude_code", "opus", "Claude Opus"),
              ("chatgpt", "", "ChatGPT"), ("gemini", "", "Gemini"))
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
FOLDER_ROWS, FOLDER_COLS = 4, 3


@dataclass
class Panel:
    id: int
    kind: str                 # window clock system note image model music folder file picker
    title: str
    x: float                  # centre, fraction of the screen width
    y: float                  # centre, fraction of the screen height
    w: float                  # width at the front (z = 0), fraction of the screen width
    h: float                  # height at the front, fraction of the screen height
    z: float = 0.3            # 0 = front, 1 = far back
    ref: object = None        # window handle for "window"
    source: tuple | None = None
    vx: float = 0.0           # screen fractions per second
    vy: float = 0.0
    flung: bool = False       # thrown hard: may leave the screen (and then goes to the shelf / the bin)
    stashed: bool = False
    touched: float = 0.0      # last time a hand held it: on top among panels at the same depth
    data: dict = field(default_factory=dict)
    ver: int = 0              # bumped when what is shown inside changes


@dataclass
class Grip:
    panel: int
    t0: float
    start: tuple              # hand point when it closed
    offset: tuple             # panel centre minus hand point, at the grip's depth
    size0: float              # palm size when it closed
    z0: float
    moved: bool = False
    tile: int | None = None   # grabbed an entry inside the panel (folder, music, picker)
    touch: bool = False       # a pinch (on the glass desk): held still it presses; moved, it carries the panel
    pressed: bool = False     # the press already happened (while the fingers were still together)


@dataclass
class Hit:
    panel: Panel | None
    where: str = "none"       # none | body | tile | shelf | new
    tile: int | None = None


def shelf_rects(n: int) -> list:
    w, h = SHELF_TAB
    total = n * w + max(0, n - 1) * SHELF_GAP
    x0 = 0.5 - total / 2
    return [(x0 + i * (w + SHELF_GAP), SHELF_Y - h / 2, w, h) for i in range(n)]


def grid(rect: tuple, n: int, cols: int, top: float = 0.0, gap: float = 0.006) -> list:
    """n tiles in `cols` columns inside rect (x, y, w, h), below a strip `top` (share of the height)."""
    if n <= 0:
        return []
    x, y, w, h = rect
    rows = math.ceil(n / cols)
    pad = min(w, h) * 0.05
    x0, y0 = x + pad, y + h * top + pad
    cw = (w - 2 * pad - gap * (cols - 1)) / cols
    ch = (h * (1 - top) - 2 * pad - gap * (rows - 1)) / rows
    return [(x0 + (i % cols) * (cw + gap), y0 + (i // cols) * (ch + gap), cw, ch) for i in range(n)]


def _inside(p, rect) -> bool:
    x, y, w, h = rect
    return x <= p[0] <= x + w and y <= p[1] <= y + h


def _gap(p, rect) -> float:
    """Distance from a point to a rectangle (0 inside)."""
    x, y, w, h = rect
    dx = max(x - p[0], 0.0, p[0] - (x + w))
    dy = max(y - p[1], 0.0, p[1] - (y + h))
    return math.hypot(dx, dy)


def _kind_of(path: str) -> str:
    low = path.lower()
    if low.endswith(IMAGE_EXT):
        return "image"
    if low.endswith((".obj", ".stl", ".glb", ".gltf", ".ply")):
        return "model"
    return "file"


class GrabHand(HandSlot):
    """A hand as a grabber: palm point, how big the palm looks (= how near), open or closed."""

    def __init__(self, cfg: Config, cam_aspect: float = 4 / 3):
        self.cam_aspect = cam_aspect
        self.px = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self.py = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self.ps = OneEuroFilter(1.0, 0.5)
        super().__init__(cfg)

    def reset(self):
        super().reset()
        self.palm = (0.5, 0.5)        # filtered palm centre on the screen
        self.point = (0.5, 0.5)       # palm centre pushed ahead by the camera's delay: where the hand is now
        self.pvel = (0.0, 0.0)
        self.size = 0.1               # palm length in frame heights
        self.closed = False
        self.armed = False            # set once the hand has been seen open
        self.just_closed = False
        self.just_opened = False
        self.closed_on_empty = None   # time this hand closed over nothing (for drawing a new panel)
        self.trail = []               # (t, x, y) of the point
        for f in (getattr(self, "px", None), getattr(self, "py", None), getattr(self, "ps", None)):
            if f is not None:
                f.reset()

    def update(self, hand: Hand, t: float, cmap: CameraMap):
        cfg = self.cfg
        prev_t = self.last_t
        super().update(hand, t, cmap)
        lm = hand.lm
        u = sum(lm[i][0] for i in PALM) / len(PALM)
        v = sum(lm[i][1] for i in PALM) / len(PALM)
        raw = cmap.to_stage(u, v)
        old = self.palm if prev_t is not None else None
        self.palm = (self.px(raw[0], t), self.py(raw[1], t))
        if old is not None and prev_t is not None and t > prev_t:
            dt = t - prev_t
            v_now = ((self.palm[0] - old[0]) / dt, (self.palm[1] - old[1]) / dt)
            k = min(1.0, dt / 0.05)
            self.pvel = (self.pvel[0] + k * (v_now[0] - self.pvel[0]), self.pvel[1] + k * (v_now[1] - self.pvel[1]))
        lead = cfg.space_lead
        self.point = (self.palm[0] + self.pvel[0] * lead, self.palm[1] + self.pvel[1] * lead)
        self.trail.append((t, *self.point))
        while self.trail and t - self.trail[0][0] > 0.25:
            self.trail.pop(0)

        dx = (lm[9][0] - lm[0][0]) * self.cam_aspect
        dy = lm[9][1] - lm[0][1]
        self.size = self.ps(math.hypot(dx, dy), t)

        pinch, reach = pinch_features(hand.world)
        was = self.closed
        mean = sum(reach) / len(reach)
        # The raw pinch distance, not the smoothed one the stage uses: that costs a frame.
        if was:
            pinched = pinch <= cfg.pinch_off_index
            self.closed = pinched or not min(reach[:3]) > self.open_at()
        else:
            pinched = pinch < cfg.pinch_on_index and reach[0] > cfg.pinch_reach_min
            self.closed = pinched or self.fist or mean < self.close_at()
        if not self.armed:                       # a hand that comes into view already closed grabs nothing
            if self.closed:
                self.closed = False
            else:
                self.armed = True
        self.just_closed = self.closed and not was
        self.just_opened = was and not self.closed

    def close_at(self) -> float:
        """Average fingertip reach under which the hand counts as closed. Follows the hand setup:
        a claw (the loosest closed hand) must count, so it sits just under the claw's upper bound."""
        return self.cfg.claw_max - 0.06

    def open_at(self) -> float:
        return self.cfg.claw_open

    def velocity(self, t: float, window: float = 0.12) -> tuple:
        recent = [p for p in self.trail if p[0] >= t - window - 1e-9]
        if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
            return 0.0, 0.0
        dt = recent[-1][0] - recent[0][0]
        return (recent[-1][1] - recent[0][1]) / dt, (recent[-1][2] - recent[0][2]) / dt


def tr_confirm(action: str) -> str:
    """Label of a power button waiting for its second tap (the view translates it)."""
    return "confirm:" + action


def _no_files(kind, arg):
    return []


class SpaceEngine:
    _match = StageEngine._match       # the same two-hand identity tracking as the stage

    def __init__(self, cfg: Config, cmap: CameraMap, aspect: float, cam_aspect: float = 4 / 3, provide=None):
        """provide(kind, arg): ("folder", path) -> [(name, path, is_dir)], ("images", folder) -> [path],
        ("models", None) -> [(label, ref)], ("home", None) -> path of the folder a new folder panel shows."""
        self.cfg, self.cmap, self.aspect = cfg, cmap, aspect
        self.provide = provide or _no_files
        self.slots = [GrabHand(cfg, cam_aspect), GrabHand(cfg, cam_aspect)]
        self.panels: list[Panel] = []
        self.grips: dict[int, Grip] = {}
        self.pair = None                  # (distance0, midpoint0, w0, h0, x0, y0) while two hands hold one panel
        self.draft = None                 # (slot a, slot b) while two hands draw a new panel
        self.hover = set()
        self.hover_tile = None            # (panel id, tile index) under an open hand
        self.editing = None               # the note the keyboard types into
        self.ai_current = ("claude_code", "")   # JARVIS's AI now, ticked on the AI panel
        self.dirty = False                # something worth saving changed
        self.hidden = False               # all panels away (clap / Ctrl+Alt+G / JARVIS): a clean screen
        self.last_t = None
        self.t = 0.0
        self._next_id = 1000

    # ------------------------------------------------------------------ calling them all up / away
    def summon(self) -> bool:
        """Bring every panel out at once (the shelf ones to their place in the layout). True if it changed."""
        if not self.hidden and not any(p.stashed and p.kind != "window" for p in self.panels):
            return False
        self.hidden = False
        taken = [(p.x, p.y) for p in self.panels if not p.stashed]
        n = 0
        for p in self.panels:
            if not p.stashed or p.kind == "window":
                continue
            slot = SUMMON_SLOTS.get(p.kind)
            if slot is None or any(abs(x - slot[0]) < 0.08 and abs(y - slot[1]) < 0.08 for x, y in taken):
                slot = (0.36 + 0.07 * (n % 5), 0.30 + 0.10 * (n // 5 % 4), p.w, p.h)   # spread around the middle
                n += 1
            p.x, p.y, p.w, p.h = slot
            p.stashed, p.vx, p.vy, p.flung = False, 0.0, 0.0, False
            taken.append((p.x, p.y))
            self.keep_on_screen(p)
        self.grips.clear()
        self.dirty = True
        return True

    def dismiss(self) -> bool:
        """Every panel away at once; they come back where they were. True if it changed."""
        if self.hidden:
            return False
        self.hidden = True
        self.grips.clear()
        self.editing = None
        return True

    def toggle_summon(self) -> bool:
        """True = the panels are out now."""
        if self.hidden:
            self.summon()
            return True
        self.dismiss()
        return False

    # ------------------------------------------------------------------ panels
    def panel(self, pid):
        return next((p for p in self.panels if p.id == pid), None)

    def scale(self, z: float) -> float:
        return 1.0 - self.cfg.space_shrink * max(0.0, min(1.0, z))

    def rect(self, p: Panel) -> tuple:
        """(x, y, w, h) as seen: further back = smaller."""
        s = self.scale(p.z)
        return p.x - p.w * s / 2, p.y - p.h * s / 2, p.w * s, p.h * s

    def shelf(self) -> list:
        return [p for p in self.panels if p.stashed]

    def shelf_tabs(self) -> list:
        """(panel or None for the + tab, rect)."""
        items = self.shelf() + [None]
        return list(zip(items, shelf_rects(len(items))))

    def front_to_back(self) -> list:
        return sorted((p for p in self.panels if not p.stashed), key=lambda p: (p.z, -p.touched))

    def tiles(self, p: Panel) -> list:
        """(rect, label, action) of the entries inside a panel, in screen fractions."""
        r = self.rect(p)
        if p.kind == "picker":
            return [(t, c, ("make", c)) for t, c in zip(grid(r, len(CHOICES), 4, top=0.0), CHOICES)]
        if p.kind == "apps":
            apps = p.data.get("items", [])
            page = p.data.get("page", 0)
            per = APPS_PAGE - 1 if len(apps) > APPS_PAGE else APPS_PAGE
            shown = apps[page * per:(page + 1) * per]
            entries = [(label, ("app", path)) for label, path in shown]
            if len(apps) > APPS_PAGE:
                entries.append((f"{page + 1}/{math.ceil(len(apps) / per)}  ▸", ("page", None)))
            return [(t, label, act) for t, (label, act) in zip(grid(r, len(entries), 4, top=0.0), entries)]
        if p.kind == "windows":
            wins = p.data.get("items", [])[:9]
            return [(t, ("–  " if mini else "") + title, ("win", hwnd))
                    for t, (title, hwnd, mini) in zip(grid(r, len(wins), 3, top=0.0), wins)]
        if p.kind == "power":
            armed = p.data.get("armed")
            return [(t, (tr_confirm(a) if a == armed else a), ("power", a))
                    for t, (a, _glyph) in zip(grid(r, len(POWER), 3, top=0.0), POWER)]
        if p.kind == "ai":
            return [(t, ("✓ " if (b, m) == tuple(self.ai_current) else "") + label, ("ai", (b, m)))
                    for t, (b, m, label) in zip(grid(r, len(AI_CHOICES), 3, top=0.0), AI_CHOICES)]
        if p.kind == "music":
            acts = (("◀◀", "prev"), ("▶ ||", "play"), ("▶▶", "next"))     # plain glyphs: Windows turns ⏯ into emoji
            return [(t, label, ("media", a)) for t, (label, a) in zip(grid(r, 3, 3, top=0.45), acts)]
        if p.kind == "folder":
            items = p.data.get("items", [])
            entries = []
            if p.data.get("parent"):
                entries.append(("⬑", ("up", p.data["parent"])))
            for name, path, is_dir in items[:FOLDER_ROWS * FOLDER_COLS - len(entries)]:
                entries.append((name, ("dir" if is_dir else "file", path)))
            return [(t, label, act) for t, (label, act) in zip(grid(r, len(entries), FOLDER_COLS, top=0.16), entries)]
        return []

    def hit(self, point) -> Hit:
        """What is under a point. A near miss (within space_magnet) still catches the nearest panel."""
        if self.hidden:
            return Hit(None)
        for p, r in self.shelf_tabs():
            if _inside(point, r):
                return Hit(p, "shelf" if p is not None else "new")
        near, best = None, None
        for p in self.front_to_back():
            r = self.rect(p)
            if _inside(point, r):
                tiles = self.tiles(p)
                for i, (tr, _label, _act) in enumerate(tiles):
                    if _inside(point, tr):
                        return Hit(p, "tile", i)
                # Between two buttons, or just off one: the nearest button within reach still gets it.
                pt = (point[0] * self.aspect, point[1])
                gaps = [(_gap(pt, (tr[0] * self.aspect, tr[1], tr[2] * self.aspect, tr[3])), i)
                        for i, (tr, _label, _act) in enumerate(tiles)]
                if gaps:
                    d, i = min(gaps)
                    if d <= self.cfg.space_tile_magnet:
                        return Hit(p, "tile", i)
                return Hit(p, "body")
            d = _gap((point[0] * self.aspect, point[1]), (r[0] * self.aspect, r[1], r[2] * self.aspect, r[3]))
            if d <= self.cfg.space_magnet and (best is None or d < best):
                near, best = p, d
        return Hit(near, "body") if near is not None else Hit(None)

    def add(self, p: Panel) -> Panel:
        self.panels.append(p)
        self.dirty = True
        return p

    def new_id(self) -> int:
        used = {p.id for p in self.panels}
        while self._next_id in used:
            self._next_id += 1
        self._next_id += 1
        return self._next_id - 1

    def sync(self, panels: list) -> None:
        """Bring in the current windows (opened or closed), keeping where ours already are.
        Panels you made are not windows and are kept as they are."""
        old = {p.id: p for p in self.panels}
        out = []
        for p in panels:
            mine = old.get(p.id)
            if mine is not None:
                mine.title, mine.source = p.title, p.source
                out.append(mine)
            elif p.kind == "window" or not any(q.kind == p.kind for q in self.panels):
                out.append(p)
        ids = {p.id for p in out}
        held = {g.panel for g in self.grips.values()}
        out += [p for p in self.panels if p.id not in ids and (p.kind != "window" or p.id in held)]
        self.panels = out

    # ------------------------------------------------------------------ making and filling panels
    def fill(self, p: Panel, kind: str, path: str | None = None) -> None:
        """Turn a panel into `kind` and give it something to show."""
        p.kind, p.ver = kind, p.ver + 1
        p.title = ""
        if kind == "note":
            p.data = {"text": p.data.get("text", "")}
        elif kind == "image":
            folder = os.path.dirname(path) if path else self.provide("pictures", None)
            images = self.provide("images", folder) if folder else []
            index = images.index(path) if path in images else 0
            p.data = {"folder": folder, "path": path or (images[0] if images else None), "index": index}
            p.title = os.path.basename(p.data["path"] or "")
        elif kind == "model":
            models = self.provide("models", None)
            ref = path or (models[0][1] if models else "builtin:knot")
            p.data = {"ref": ref}
            p.title = next((label for label, r in models if r == ref), os.path.basename(ref))
        elif kind == "folder":
            self._open_folder(p, path or self.provide("home", None))
        elif kind == "file":
            p.data = {"path": path}
            p.title = os.path.basename(path or "")
        elif kind in ("apps", "windows"):
            p.data = {"items": self.provide(kind, None), "page": 0}
        elif kind in ("music", "clock", "system", "picker", "power", "ai"):
            p.data = {}
        self.dirty = True

    def _open_folder(self, p: Panel, path: str | None) -> None:
        items = self.provide("folder", path) if path else []
        parent = os.path.dirname(path.rstrip("\\/")) if path else None
        home = os.path.expanduser("~").rstrip("\\/")
        if parent == path or not parent or path.rstrip("\\/").lower() == home.lower():
            parent = None                            # your home folder (or a drive's root): nowhere further up,
                                                     # so the panel never wanders into Windows' own folders
        p.data = {"path": path, "items": items, "parent": parent}
        p.title = os.path.basename(path.rstrip("\\/")) if path else ""
        p.ver += 1

    def refresh(self) -> None:
        """Re-read what can change behind our back: the open windows, the app list; a power button
        left armed calms down again."""
        for p in self.panels:
            if p.kind in ("windows", "apps"):
                items = self.provide(p.kind, None)
                if items != p.data.get("items"):
                    p.data["items"] = items
                    p.ver += 1
            elif p.kind == "power" and p.data.get("armed") and self.t - p.data.get("armed_at", 0) > CONFIRM_TIME:
                p.data.pop("armed", None)
                p.ver += 1

    def type_text(self, text: str) -> None:
        """Keyboard input for the note being edited: text, "\\b" = backspace, "\\n" = new line."""
        p = self.panel(self.editing)
        if p is None or p.kind != "note":
            self.editing = None
            return
        body = p.data.get("text", "")
        for ch in text:
            body = body[:-1] if ch == "\b" else body + ch
        p.data["text"] = body[:2000]
        p.ver += 1
        self.dirty = True

    # ------------------------------------------------------------------ hands
    def update(self, hands: list[Hand], t: float) -> list[tuple]:
        """Track the hands here (the glass space on its own, and the tests)."""
        for s in self.slots:
            s.just_closed = s.just_opened = False
        return self._step(t, self._match(hands, t))

    def drive(self, t: float, lost=()) -> list[tuple]:
        """The hands are tracked elsewhere (the glass desk): self.slots already hold this frame's state.
        `lost` = slots whose hand just went away."""
        return self._step(t, list(lost))

    def _step(self, t: float, lost: list) -> list[tuple]:
        cmds: list[tuple] = []
        dt = min(t - self.last_t, 0.1) if self.last_t is not None else 0.0
        self.last_t = self.t = t
        for si in lost:
            if si in self.grips:
                self._let_go(si, t, cmds, quiet=True)
            if self.draft is not None and si in self.draft:
                self.draft = None
            self.slots[si].reset()

        self._coast(dt, cmds)

        live = [si for si, s in enumerate(self.slots) if s.active and s.lost_since is None]
        for si in live:
            s = self.slots[si]
            if not s.closed:
                s.closed_on_empty = None
            if s.just_opened:
                if self.draft is not None and si in self.draft:
                    self._finish_draft(t, cmds)
                elif si in self.grips:
                    self._let_go(si, t, cmds)
        for si in live:
            s = self.slots[si]
            if s.just_closed and si not in self.grips:
                self._take(si, t, cmds)
        self._maybe_draft(live, t)
        self._carry(t, cmds, dt)

        self.hover, self.hover_tile = set(), None
        for si in live:
            s = self.slots[si]
            if not s.closed:
                h = self.hit(s.point)
                if h.panel is not None:
                    self.hover.add(h.panel.id)
                    if h.where == "tile":
                        self.hover_tile = (h.panel.id, h.tile)
        return cmds

    def _take(self, si: int, t: float, cmds: list) -> None:
        s = self.slots[si]
        h = self.hit(s.point)
        if h.where == "none":
            s.closed_on_empty = t
            return
        if h.where == "new":                        # the + tab: a fresh panel straight into the hand
            p = self.add(Panel(self.new_id(), "picker", "", s.point[0], s.point[1], 0.24, 0.26, z=0.15))
            cmds.append(("create", p.id))
            self._grip(si, p, t, moved=True)
            cmds.append(("grab", p.id))
            return
        p = h.panel
        if h.where == "shelf":
            p.stashed = False
            p.z = min(p.z, 0.25)
            p.x, p.y = s.point
        if h.where == "tile" and p.kind == "picker":   # choosing what goes in a new panel: done on touch
            self.fill(p, self.tiles(p)[h.tile][2][1])
            cmds.append(("make", p.id))
            self._grip(si, p, t, moved=True)
            cmds.append(("grab", p.id))
            return
        if getattr(s, "touch", False) and h.where in ("body", "tile"):
            # A pinch touches: it presses a button or uses the panel, it does not pick the panel up.
            self._grip(si, p, t, tile=h.tile if h.where == "tile" else None, touch=True)
            cmds.append(("press", p.id))
            return
        others = [k for k, g in self.grips.items() if g.panel == p.id and not g.touch]
        self._grip(si, p, t, moved=h.where == "shelf" or bool(others),
                   tile=h.tile if h.where == "tile" and not others else None)
        if others:                                  # second hand on the same panel: stretch it
            other = others[0]
            a, b = self.slots[other].point, s.point
            self.pair = (max(self._apart(a, b), 1e-3), self._mid(a, b), p.w, p.h, p.x, p.y)
            self.grips[other].moved = True
            self.grips[other].tile = None
        cmds.append(("grab", p.id))

    def _grip(self, si, p, t, moved=False, tile=None, touch=False):
        s = self.slots[si]
        p.vx = p.vy = 0.0
        p.flung = False
        p.touched = t
        self.grips[si] = Grip(p.id, t, s.point, (p.x - s.point[0], p.y - s.point[1]), s.size, p.z,
                              moved=moved, tile=tile, touch=touch)

    def _maybe_draft(self, live: list, t: float) -> None:
        """Both hands closed over empty space within a moment of each other: start drawing a panel."""
        if self.draft is not None or len(live) < 2:
            return
        a, b = live[0], live[1]
        sa, sb = self.slots[a], self.slots[b]
        if (sa.closed and sb.closed and sa.closed_on_empty is not None and sb.closed_on_empty is not None
                and abs(sa.closed_on_empty - sb.closed_on_empty) <= self.cfg.space_pair_time):
            self.draft = (a, b)

    def draft_rect(self):
        if self.draft is None:
            return None
        (ax, ay), (bx, by) = (self.slots[i].point for i in self.draft)
        return min(ax, bx), min(ay, by), abs(ax - bx), abs(ay - by)

    def _finish_draft(self, t: float, cmds: list) -> None:
        x, y, w, h = self.draft_rect()
        self.draft = None
        for s in self.slots:
            s.closed_on_empty = None
        if w * self.aspect < self.cfg.space_min_new or h < self.cfg.space_min_new:
            return                                   # too small: the hands were just closing near each other
        z = 0.15
        s = self.scale(z)
        p = self.add(Panel(self.new_id(), "picker", "", x + w / 2, y + h / 2, w / s, h / s, z=z, touched=t))
        cmds.append(("create", p.id))

    def _let_go(self, si: int, t: float, cmds: list, quiet: bool = False) -> None:
        cfg = self.cfg
        g = self.grips.pop(si)
        p = self.panel(g.panel)
        if self.pair is not None:
            self.pair = None
            rest = [k for k, h in self.grips.items() if h.panel == g.panel]
            for k in rest:                          # the hand still holding takes over without a jump
                o = self.slots[k]
                h = self.grips[k]
                h.offset = (p.x - o.point[0], p.y - o.point[1]) if p else h.offset
                h.size0, h.z0 = o.size, p.z if p else h.z0
            if p is not None and not quiet:
                cmds.append(("drop", p.id))
                self.dirty = True
            return
        if p is None:
            return
        if quiet:                                   # the hand went out of the camera's view
            p.vx = p.vy = 0.0
            if g.moved and not g.touch and self.off_screen(p):
                self._throw_away(p, cmds)           # carrying it out of the screen takes the hand out of view too
            else:
                self.keep_on_screen(p)              # never left stranded half off the screen
            return
        if g.touch:                                 # a press: use it, unless the hand wandered off meanwhile
            if not g.moved and not g.pressed:
                self._use(p, g, cmds)
            return
        if g.moved and self.off_screen(p, self.slots[si].point):
            self._throw_away(p, cmds)               # dragged out of the screen and let go there
            return
        if not g.moved and t - g.t0 <= cfg.space_tap_time:
            # On the glass desk a pinch presses and a fist only grabs: a quick grab never presses a button
            # (one once hit "Quit" on the power panel). The space-only mode has just one gesture, so there it does.
            if not hasattr(self.slots[si], "touch") or p.kind == "window":
                self._use(p, g, cmds)
            return
        self.dirty = True
        vx, vy = self.slots[si].velocity(t)
        p.vx, p.vy = vx, vy
        speed = math.hypot(vx * self.aspect, vy)
        if speed >= cfg.space_fling:
            p.flung = True
            cmds.append(("throw", p.id))
        else:
            cmds.append(("drop", p.id))

    def _use(self, p: Panel, g: Grip, cmds: list) -> None:
        """Grab and let go without moving: do what this panel does."""
        if g.tile is not None:
            tiles = self.tiles(p)
            if g.tile < len(tiles):
                kind, arg = tiles[g.tile][2]
                if kind == "media":
                    cmds.append(("media", arg))
                elif kind in ("dir", "up"):
                    self._open_folder(p, arg)
                    self.dirty = True
                elif kind in ("file", "app"):
                    cmds.append(("open_file", arg))
                elif kind == "win":
                    cmds.append(("focus", arg))
                elif kind == "ai":
                    cmds.append(("ai", arg))
                elif kind == "page":
                    per = APPS_PAGE - 1
                    pages = max(1, math.ceil(len(p.data.get("items", [])) / per))
                    p.data["page"] = (p.data.get("page", 0) + 1) % pages
                    p.ver += 1
                elif kind == "power":
                    if arg in CONFIRM and (p.data.get("armed") != arg or self.t - p.data.get("armed_at", -1e9) > CONFIRM_TIME):
                        p.data.update(armed=arg, armed_at=self.t)      # a second tap does it: nobody shuts down by accident
                        p.ver += 1
                        cmds.append(("armed", arg))
                    else:
                        p.data.pop("armed", None)
                        p.ver += 1
                        cmds.append(("power", arg))
                return
        if p.kind == "window":
            cmds.append(("open", p.id))
        elif p.kind == "note":
            self.editing = None if self.editing == p.id else p.id
            p.ver += 1
            cmds.append(("edit", self.editing))
        elif p.kind == "image":
            images = self.provide("images", p.data.get("folder")) if p.data.get("folder") else []
            if images:
                i = (p.data.get("index", 0) + 1) % len(images)
                p.data.update(index=i, path=images[i])
                p.title = os.path.basename(images[i])
                p.ver += 1
                cmds.append(("next", p.id))
        elif p.kind == "model":
            models = self.provide("models", None)
            refs = [r for _, r in models]
            if refs:
                i = (refs.index(p.data.get("ref")) + 1) % len(refs) if p.data.get("ref") in refs else 0
                p.data["ref"] = refs[i]
                p.title = models[i][0]
                p.ver += 1
                cmds.append(("next", p.id))
        elif p.kind == "music":
            cmds.append(("media", "play"))
        elif p.kind == "file":
            cmds.append(("open_file", p.data.get("path")))
        self.dirty = True

    def _carry(self, t: float, cmds: list, dt: float = 0.0) -> None:
        cfg = self.cfg
        held = {}
        for si, g in self.grips.items():
            held.setdefault(g.panel, []).append(si)
        for pid, sis in held.items():
            p = self.panel(pid)
            if p is None:
                for si in sis:
                    self.grips.pop(si, None)
                continue
            p.touched = t
            if len(sis) == 2 and self.pair is not None:
                a, b = self.slots[sis[0]].point, self.slots[sis[1]].point
                d0, m0, w0, h0, x0, y0 = self.pair
                k = self._apart(a, b) / d0
                h = min(max(h0 * k, cfg.space_min_h), cfg.space_max_h)
                k = h / h0
                p.w, p.h = w0 * k, h
                m = self._mid(a, b)
                p.x, p.y = x0 + (m[0] - m0[0]), y0 + (m[1] - m0[1])
                va, vb = self.slots[sis[0]].pvel, self.slots[sis[1]].pvel
                p.vx, p.vy = (va[0] + vb[0]) / 2, (va[1] + vb[1]) / 2
                continue
            si = sis[0]
            s, g = self.slots[si], self.grips[si]
            if g.touch:
                far = math.dist(s.point, g.start)
                # A pinch on a panel presses when the fingers open (not earlier, as on the desktop): it may be
                # the start of moving the panel, and an early press once locked the PC from the power panel.
                if not g.moved and far > cfg.space_press_move:
                    g.moved = True
                    if g.tile is not None and p.kind == "folder":
                        self._pull_out(si, p, g, t, cmds)    # a file pinched and pulled comes out of its folder
                        continue
                    # pinched and pulled: the panel follows the hand from here, like a drag on the desktop
                    g.touch, g.tile = False, None            # the grip's offset is from where the pinch began,
                    cmds.append(("grab", p.id))              # so the panel catches up with the hand
                continue
            if not g.moved and math.dist(s.point, g.start) > cfg.space_tap_move:
                g.moved = True
                if g.tile is not None and p.kind == "folder":
                    self._pull_out(si, p, g, t, cmds)    # an entry dragged out of a folder becomes a panel
                    continue
                g.tile = None                            # moved: it is the panel being carried, not a button
            if g.tile is not None and not g.moved:
                continue                                 # a finger on an entry: the panel stays still

            ratio = max(s.size, 1e-4) / max(g.size0, 1e-4)
            step = math.log(ratio)
            dead = cfg.space_depth_dead
            step = 0.0 if abs(step) < dead else step - math.copysign(dead, step)
            z = min(max(g.z0 - step * cfg.space_depth_gain, 0.0), 1.0)
            if abs(z - g.z0) > 0.06:
                g.moved = True
            if g.moved and dt > 0:
                # The pointer stops at the screen's edge. A panel held by its outer side would then never get
                # far enough out to throw away, so while the hand pushes at an edge the panel slides on out
                # until the hand holds it by the middle (half of it out: enough to throw away).
                ox, oy = g.offset
                f = 1.0 - math.exp(-dt / EDGE_SLIDE)
                if (s.point[0] >= 1 - EDGE and ox < 0) or (s.point[0] <= EDGE and ox > 0):
                    ox -= ox * f
                if (s.point[1] >= 1 - EDGE and oy < 0) or (s.point[1] <= EDGE and oy > 0):
                    oy -= oy * f
                g.offset = (ox, oy)
            k = self.scale(z) / self.scale(g.z0)       # the panel grows / shrinks round the hand
            p.z = z
            p.x, p.y = s.point[0] + g.offset[0] * k, s.point[1] + g.offset[1] * k
            p.vx, p.vy = s.pvel

    def _pull_out(self, si: int, folder: Panel, g: Grip, t: float, cmds: list) -> None:
        tiles = self.tiles(folder)
        kind, path = tiles[g.tile][2] if g.tile < len(tiles) else ("", None)
        self.grips.pop(si)
        if kind not in ("dir", "file") or not path:
            self._grip(si, folder, t, moved=True)
            return
        s = self.slots[si]
        new = Panel(self.new_id(), "folder", "", s.point[0], s.point[1], 0.22, 0.30, z=max(folder.z - 0.1, 0.0))
        if kind == "dir":
            self.fill(new, "folder", path)
        else:
            what = _kind_of(path)
            if what == "image":
                new.w, new.h = 0.24, 0.30
            elif what == "file":
                new.w, new.h = 0.14, 0.16
            self.fill(new, what, path)
        self.add(new)
        cmds.append(("create", new.id))
        self._grip(si, new, t, moved=True)
        cmds.append(("grab", new.id))

    # ------------------------------------------------------------------ physics
    def _coast(self, dt: float, cmds: list) -> None:
        if dt <= 0:
            return
        cfg = self.cfg
        held = {g.panel for g in self.grips.values()}
        keep = math.exp(-cfg.space_friction * dt)
        for p in list(self.panels):
            if p.stashed or p.id in held or (p.vx == 0.0 and p.vy == 0.0):
                continue
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vx *= keep
            p.vy *= keep
            x, y, w, h = self.rect(p)
            if p.flung:
                if x > 1.0 or x + w < 0.0 or y > 1.0 or y + h < 0.0:   # gone off the screen: thrown away
                    self._throw_away(p, cmds)                         # (a real window goes to the shelf)
                    continue
            else:
                bounce = cfg.space_bounce
                if x < 0 and p.vx < 0:
                    p.x += -x
                    p.vx = -p.vx * bounce
                elif x + w > 1 and p.vx > 0:
                    p.x -= x + w - 1
                    p.vx = -p.vx * bounce
                if y < 0 and p.vy < 0:
                    p.y += -y
                    p.vy = -p.vy * bounce
                elif y + h > SHELF_Y - SHELF_TAB[1] and p.vy > 0:
                    p.y -= y + h - (SHELF_Y - SHELF_TAB[1])
                    p.vy = -p.vy * bounce
            if math.hypot(p.vx * self.aspect, p.vy) < 0.01:
                p.vx = p.vy = 0.0
                if p.flung:
                    p.flung = False
                self.keep_on_screen(p)                  # came to rest: never half off the screen

    # ------------------------------------------------------------------ throwing away: off the screen
    def off_screen(self, p: Panel, point=None) -> bool:
        """Carried far enough out of the screen that letting go throws it away: over 40 % of the panel is
        past an edge. (Not "the hand at the edge": the pointer often sits at x = 1.00 near the camera's edge.)"""
        x, y, w, h = self.rect(p)
        vis_w = max(0.0, min(x + w, 1.0) - max(x, 0.0))
        vis_h = max(0.0, min(y + h, 1.0) - max(y, 0.0))
        return vis_w * vis_h < OFF_SCREEN * w * h

    def _carrying(self) -> list:
        """Hands moving a panel on their own (not pressing it, not stretching it with the other hand)."""
        if self.pair is not None:
            return []
        return [si for si, g in self.grips.items() if not g.touch and g.moved]

    def _throw_away(self, p: Panel, cmds: list) -> None:
        if p.kind not in MADE:                       # a real window is never closed this way: it goes to the shelf
            self._stash(p, cmds)
            return
        self.panels.remove(p)
        if self.editing == p.id:
            self.editing = None
        cmds.append(("delete", p.id))
        self.dirty = True

    def _stash(self, p: Panel, cmds: list) -> None:
        p.stashed, p.flung = True, False
        p.vx = p.vy = 0.0
        p.x, p.y = 0.5, 0.45
        cmds.append(("stash", p.id))
        self.dirty = True

    # ------------------------------------------------------------------ helpers
    def _apart(self, a, b) -> float:
        return math.hypot((a[0] - b[0]) * self.aspect, a[1] - b[1])

    @staticmethod
    def _mid(a, b):
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2

    def release_all(self) -> None:
        self.grips.clear()
        self.pair = None
        self.draft = None
        for s in self.slots:
            s.reset()

    # ------------------------------------------------------------------ saving
    def dump(self) -> list:
        """The panels you made, to keep between runs (windows come and go with their programs)."""
        out = []
        for p in self.panels:
            if p.kind == "window":
                continue
            data = {k: v for k, v in p.data.items() if k not in ("items", "armed", "armed_at")}
            out.append({"id": p.id, "kind": p.kind, "title": p.title, "x": round(p.x, 4), "y": round(p.y, 4),
                        "w": round(p.w, 4), "h": round(p.h, 4), "z": round(p.z, 3), "stashed": p.stashed, "data": data})
        return out

    def keep_on_screen(self, p: Panel) -> None:
        """At least most of a panel stays on the screen, above the shelf."""
        x, y, w, h = self.rect(p)
        dx = max(0.0, 0.01 - x) - max(0.0, x + w - 0.99)
        dy = max(0.0, 0.035 - y) - max(0.0, y + h - (SHELF_Y - SHELF_TAB[1]))
        if dx or dy:
            p.x += dx
            p.y += dy
            p.vx = p.vy = 0.0
            self.dirty = True

    def load(self, saved: list) -> None:
        keep = [p for p in self.panels if p.kind == "window"]
        made = []
        for d in saved:
            try:
                p = Panel(int(d["id"]), str(d["kind"]), str(d.get("title", "")), float(d["x"]), float(d["y"]),
                          float(d["w"]), float(d["h"]), z=float(d.get("z", 0.3)), stashed=bool(d.get("stashed")),
                          data=dict(d.get("data") or {}))
            except (KeyError, TypeError, ValueError):
                continue
            if p.kind == "folder":
                self._open_folder(p, p.data.get("path"))
            elif p.kind in ("apps", "windows"):
                p.data["items"] = self.provide(p.kind, None)
            made.append(p)
        self.panels = keep + made
        # New panels get ids past the saved ones: a thrown-away id used again would mix up the fly-out
        # of the old panel with the new one on the screen.
        self._next_id = max([self._next_id] + [p.id + 1 for p in made])
        for p in made:
            if not p.stashed:
                self.keep_on_screen(p)
        self.dirty = False

    # ------------------------------------------------------------------ for the screen
    def view(self) -> dict:
        held = {g.panel for g in self.grips.values()}
        panels = []
        for p in reversed(self.front_to_back()):        # back to front: drawing order
            state = "held" if p.id in held else "hover" if p.id in self.hover else ""
            tiles = [(r, label, act[0]) for r, label, act in self.tiles(p)]
            hover_tile = self.hover_tile[1] if self.hover_tile and self.hover_tile[0] == p.id else None
            panels.append({"id": p.id, "kind": p.kind, "title": p.title, "rect": self.rect(p), "z": p.z,
                           "vx": p.vx, "vy": p.vy, "state": state, "ref": p.ref, "source": p.source,
                           "data": dict(p.data), "tiles": tiles, "hover_tile": hover_tile, "ver": p.ver,
                           "editing": p.id == self.editing})
        shelf = [{"id": p.id if p else NEW, "kind": p.kind if p else "new", "title": p.title if p else "+",
                  "rect": r, "state": "hover" if p and p.id in self.hover else ""}
                 for p, r in self.shelf_tabs()]
        hands = []
        for si, s in enumerate(self.slots):
            if s.active and s.lost_since is None:
                hands.append({"point": s.point, "closed": s.closed, "holding": si in self.grips,
                              "pair": self.pair is not None and si in self.grips})
        doomed = {self.grips[si].panel for si in self._carrying()
                  if self.panel(self.grips[si].panel) is not None
                  and self.off_screen(self.panel(self.grips[si].panel), self.slots[si].point)}
        for pnl in panels:                          # out of the screen far enough: letting go throws it away
            if pnl["id"] in doomed:
                pnl["state"] = "bin"
        if self.hidden:
            panels, shelf = [], []
        return {"panels": panels, "shelf": shelf, "hands": hands, "t": self.t, "draft": self.draft_rect()}


# ---------------------------------------------------------------------------- the first layout
def initial_panels(windows: list, screen: tuple, cfg: Config) -> list:
    """Panels for the open windows (on an arc, alternating depth), a clock and a system panel.
    windows: objects with hwnd, title, rect (l, t, r, b) in pixels, source, minimized."""
    sw, sh = screen
    out = [Panel(CLOCK, "clock", "", 0.12, 0.13, 0.17, 0.15, z=0.25),
           Panel(SYSTEM, "system", "", 0.88, 0.13, 0.17, 0.15, z=0.25)]
    shown = [w for w in windows if not w.minimized][:cfg.space_max_windows]
    n = len(shown)
    for i, win in enumerate(shown):
        l, t, r, b = win.rect
        aspect_px = max(r - l, 1) / max(b - t, 1)
        h = 0.32
        w = min(h * aspect_px * sh / sw, 0.30)
        h = w * sw / sh / aspect_px
        k = (i + 0.5) / n if n else 0.5
        x = 0.16 + 0.68 * k
        y = 0.50 + 0.10 * math.cos((k - 0.5) * math.pi) - 0.05
        z = 0.15 if i % 2 == 0 else 0.5
        out.append(Panel(win.hwnd, "window", win.title, x, y, w, h, z=z, ref=win.hwnd, source=win.source))
    for win in [w for w in windows if w.minimized][:cfg.space_max_windows]:
        out.append(Panel(win.hwnd, "window", win.title, 0.5, 0.45, 0.30, 0.30, z=0.2, ref=win.hwnd,
                         source=win.source, stashed=True))
    return out
