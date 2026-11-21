"""The ring: a floating menu for the real computer.

Tap the ring and four orbs bloom: open windows, your folders, your apps, 3D models. Tap an orb for a
glass list; tap a row to bring that window up, walk into that folder, or open that file / app.
Pinch-drag the ring to move it, pinch-drag a list to scroll it.

Pure logic. Positions are fractions of the screen (x of the width, y of the height);
radii and distances are fractions of the screen height.
"""
import math
from dataclasses import dataclass
from .i18n import tr

R_RING = 0.075
R_ORB = 0.052
ORB_DIST = 0.19
ANGLES = (-60, -20, 20, 60)
PANEL_W = 0.25
ROW_H = 0.060
HEAD_H = 0.062
ROWS = 8
GAP = 0.035
ORBS = (("windows", tr("Windows")), ("files", tr("Files")), ("apps", tr("Apps")), ("models", "3D"))


@dataclass
class Item:
    label: str
    kind: str       # window | dir | file | app | model | back
    ref: object = None


class Hub:
    def __init__(self, cfg, aspect: float, provider):
        """provider(kind, arg) -> list[Item]; kind is "windows", "files" (arg = folder or None) or "apps"."""
        self.aspect = aspect
        self.provider = provider
        self.pos = (cfg.hub_x, cfg.hub_y)
        self.state = "closed"        # closed | orbs | panel
        self.orb = None
        self.title = ""
        self.items: list[Item] = []
        self.scroll = 0.0
        self.hover = None
        self._stack: list = []

    # ---------------------------------------------------------------- geometry
    def geometry(self):
        side = 1 if self.pos[0] < 0.55 else -1
        orbs = [(self.pos[0] + side * math.cos(math.radians(a)) * ORB_DIST / self.aspect,
                 self.pos[1] + math.sin(math.radians(a)) * ORB_DIST) for a in ANGLES]
        edge = self.pos[0] + side * (ORB_DIST + R_ORB + GAP) / self.aspect
        x0 = edge if side > 0 else edge - PANEL_W
        total = HEAD_H + ROWS * ROW_H
        y0 = min(max(self.pos[1] - total / 2, 0.03), 0.97 - total)
        return side, orbs, (x0, y0, PANEL_W, total)

    def _in_circle(self, p, c, r) -> bool:
        return math.hypot((p[0] - c[0]) * self.aspect, p[1] - c[1]) <= r

    def hit(self, p):
        """What is under point p: ("ring",) ("orb", i) ("row", item index) ("panel",) or None."""
        _, orbs, (x0, y0, w, h) = self.geometry()
        if self.state == "panel" and x0 <= p[0] <= x0 + w and y0 <= p[1] <= y0 + h:
            if p[1] < y0 + HEAD_H:
                return ("panel",)
            idx = int(self.scroll) + int((p[1] - y0 - HEAD_H) // ROW_H)
            return ("row", idx) if 0 <= idx < len(self.items) else ("panel",)
        if self.state != "closed":
            for i, c in enumerate(orbs):
                if self._in_circle(p, c, R_ORB * 1.2):
                    return ("orb", i)
        if self._in_circle(p, self.pos, R_RING):
            return ("ring",)
        return None

    # ---------------------------------------------------------------- actions
    def tap(self, target) -> list[tuple]:
        kind = target[0]
        if kind == "ring":
            self.state = "orbs" if self.state == "closed" else "closed"
        elif kind == "orb":
            self.orb = ORBS[target[1]][0]
            self._stack = []
            self._load(None)
            self.state = "panel"
        elif kind == "row":
            item = self.items[target[1]]
            if item.kind == "window":
                self.close()
                return [("hub_window", item.ref)]
            if item.kind in ("file", "app"):
                self.close()
                return [("hub_open", item.ref)]
            if item.kind == "model":
                self.close()
                return [("hub_model", item.ref)]
            if item.kind == "dir":
                self._stack.append(item.ref)
                self._load(item.ref)
            elif item.kind == "back":
                self._stack.pop()
                self._load(self._stack[-1] if self._stack else None)
        return []

    def drag(self, target, dx: float, dy: float) -> None:
        if target[0] == "ring":
            self.pos = (min(max(self.pos[0] + dx, 0.06), 0.94), min(max(self.pos[1] + dy, 0.12), 0.88))
        elif target[0] in ("row", "panel"):
            limit = max(0.0, len(self.items) - ROWS)
            self.scroll = min(max(self.scroll - dy / ROW_H, 0.0), limit)     # the list follows the hand

    def set_hover(self, points) -> None:
        self.hover = None
        for p in points:
            target = self.hit(p)
            if target is not None:
                self.hover = target
                return

    def close(self) -> None:
        self.state = "closed"
        self.items = []
        self._stack = []

    def _load(self, arg) -> None:
        try:
            items = list(self.provider(self.orb, arg))
        except Exception:      # an unreadable folder must not take the whole interface down
            items = []
        label = dict(ORBS)[self.orb]
        if self.orb == "files" and arg is not None:
            items.insert(0, Item(tr("⬑  Back"), "back"))
            label = str(arg).replace("\\", "/").rstrip("/").split("/")[-1] or str(arg)
        self.items, self.title, self.scroll = items, label, 0.0

    # ---------------------------------------------------------------- drawing data
    def view(self) -> dict:
        side, orbs, rect = self.geometry()
        out = {"pos": self.pos, "state": self.state, "side": side, "ring_hover": self.hover == ("ring",),
               "orbs": [], "panel": None}
        if self.state != "closed":
            out["orbs"] = [(ORBS[i][1], x, y, self.state == "panel" and ORBS[i][0] == self.orb, self.hover == ("orb", i))
                           for i, (x, y) in enumerate(orbs)]
        if self.state == "panel":
            first = int(self.scroll)
            rows = [(it.label, it.kind, self.hover == ("row", first + k))
                    for k, it in enumerate(self.items[first:first + ROWS])]
            out["panel"] = {"rect": rect, "title": self.title, "rows": rows, "first": first, "total": len(self.items)}
        return out
