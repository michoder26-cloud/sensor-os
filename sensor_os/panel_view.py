"""Glass panels floating over the real desktop.

Each panel is its own small click-through window above everything, so the real windows behind
stay real and usable. Moving a panel moves its window (no repaint); a panel is repainted only
when it looks different. Between camera frames a moving panel is carried on along its velocity,
so it glides at the screen's rate without adding delay.

backdrop() paints the glass room (dark blue, a floor grid running to a horizon) that the
hologram wallpaper puts behind the desktop icons.
"""
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QFontMetrics, QGuiApplication, QImage, QLinearGradient, QPainter, QPen,
                           QRadialGradient)

from .hud import ORANGE, Overlay, _color, _font
from .i18n import tr
from .panels import (GOLD, ICE, KIND_NAMES, WHITE, paint_clock, paint_file, paint_music, paint_note,
                     paint_system, paint_tile)
from .shared import Snapshot

GLASS = (120, 205, 255)
GLOW = (90, 200, 255)
LEAD = 0.06          # the most a panel is carried on past its last known position, seconds
MARGIN = 26          # pixels round a panel that its glow may use
TITLE_H = 22         # pixels above a panel for its title
PAD = MARGIN + TITLE_H
_images: dict = {}   # path -> picture, shared by all panels


# ---------------------------------------------------------------- painters
def backdrop(W: int, H: int, dpr: float) -> QImage:
    img = QImage(int(W * dpr), int(H * dpr), QImage.Format_RGB32)
    img.setDevicePixelRatio(dpr)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(QPointF(W * 0.5, H * 0.42), max(W, H) * 0.75)
    g.setColorAt(0.0, QColor(10, 30, 52))
    g.setColorAt(0.55, QColor(4, 14, 28))
    g.setColorAt(1.0, QColor(1, 4, 10))
    p.fillRect(QRectF(0, 0, W, H), g)
    horizon = H * 0.60                       # a floor grid running away to a horizon: "this is a room"
    p.setPen(QPen(_color(GLOW, 22), 1))
    for i in range(-14, 15):
        p.drawLine(QPointF(W / 2 + i * W * 0.012, horizon), QPointF(W / 2 + i * W * 0.11, H))
    y, step = horizon, H * 0.012
    while y < H:
        p.setPen(QPen(_color(GLOW, 10 + 30 * (y - horizon) / (H - horizon)), 1))
        p.drawLine(QPointF(0, y), QPointF(W, y))
        step *= 1.28
        y += step
    p.setPen(QPen(_color(GLOW, 40), 1))
    p.drawLine(QPointF(0, horizon), QPointF(W, horizon))
    p.end()
    return img


def paint_glass(p, r: QRectF, pnl: dict, z: float, state: str) -> None:
    depth = 1.0 - 0.6 * z                     # far panels are fainter
    held, hover = state in ("held", "edit", "bin"), state == "hover"
    edge = ORANGE if state == "held" else GOLD if state == "edit" else (255, 80, 70) if state == "bin" else GLASS
    for grow, alpha in ((14, 10), (8, 18), (3, 38)):          # glow round it, stronger when touched
        p.setPen(QPen(_color(edge, alpha * (2.2 if held else 1.5 if hover else 1.0) * depth), grow))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 10, 10)
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    g.setColorAt(0.0, _color((40, 110, 170), 205 * depth))
    g.setColorAt(0.25, _color((14, 48, 84), 228 * depth))
    g.setColorAt(1.0, _color((6, 22, 42), 240 * depth))
    p.setPen(QPen(_color(edge, (230 if held else 170) * depth), 1.3))
    p.setBrush(g)
    p.drawRoundedRect(r, 10, 10)
    p.setPen(QPen(_color(WHITE, 70 * depth), 1))               # light catching the top edge
    p.drawLine(QPointF(r.left() + 14, r.top() + 1.5), QPointF(r.right() - 14, r.top() + 1.5))
    p.setPen(QPen(_color(edge, 255 * depth), 2.2))             # corner brackets
    k = min(18.0, r.width() * 0.12)
    for x, y, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                         (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
        p.drawLine(QPointF(x + 3 * dx, y + 3 * dy), QPointF(x + (3 + k) * dx, y + 3 * dy))
        p.drawLine(QPointF(x + 3 * dx, y + 3 * dy), QPointF(x + 3 * dx, y + (3 + k) * dy))
    title = pnl["title"] or KIND_NAMES.get(pnl["kind"], "")
    if title:
        p.setFont(_font(9, held, 1.2))
        fm = QFontMetrics(p.font())
        p.setPen(_color(ORANGE if held else ICE, 235 * depth))
        p.drawText(QPointF(r.left() + 4, r.top() - 7), fm.elidedText(title.upper(), Qt.ElideRight, int(r.width() - 8)))


def _content(r: QRectF) -> QRectF:
    return r.adjusted(6, 6, -6, -6)


def paint_image(p, c: QRectF, depth: float, path) -> None:
    img = _images.get(path)
    if img is None and path:
        img = QImage(path)
        if not img.isNull() and max(img.width(), img.height()) > 1400:
            img = img.scaled(1400, 1400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _images[path] = img
        if len(_images) > 24:
            _images.pop(next(iter(_images)))
    if img is None or img.isNull():
        p.setPen(_color(ICE, 200 * depth))
        p.setFont(_font(10))
        p.drawText(c, Qt.AlignCenter | Qt.TextWordWrap, tr("No pictures in the Pictures folder") if not path else tr("Cannot open this picture"))
        return
    k = min(c.width() / img.width(), c.height() / img.height())
    w, h = img.width() * k, img.height() * k
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setOpacity(0.55 + 0.45 * depth)
    p.drawImage(QRectF(c.center().x() - w / 2, c.center().y() - h / 2, w, h), img)
    p.setOpacity(1.0)


def paint_model(p, c: QRectF, depth: float, ref, state: str) -> None:
    from . import models3d
    from .holo_view import draw_mesh
    from .props import _rx, _ry
    try:
        V, F = models3d.load(ref or "builtin:knot", max_faces=900)
    except Exception:
        p.setPen(_color(ICE, 200 * depth))
        p.setFont(_font(10))
        p.drawText(c, Qt.AlignCenter, tr("Cannot read model"))
        return
    Vt = V @ (_rx(-0.35) @ _ry(time.perf_counter() * 0.6)).T
    draw_mesh(p, Vt, F, (c.center().x(), c.center().y()), min(c.width(), c.height()) * 0.40,
              (90, 235, 215) if state != "held" else ORANGE, depth, (225, 255, 250))


# ---------------------------------------------------------------- one panel
class PanelView(Overlay):
    def __init__(self, layer):
        super().__init__(100, 100)
        self.layer = layer
        self.pnl = None
        self.rect = QRectF()          # the panel inside this window
        self.look = None
        self._chrome = None           # (look, picture of the glass)
        self._painted_at = 0.0
        self._sec = None
        self.typing = False
        self.opacity = 0.0

    def drift(self, x: float, y: float, alpha: float) -> None:
        """Flying away (all panels sent off): move the window and fade, without repainting the glass."""
        self.move(int(x - PAD), int(y - PAD))
        if abs(alpha - self.opacity) > 0.01:
            self.opacity = alpha
            self.update()

    def place(self, pnl: dict, r: QRectF, state: str, now: float, sys_state, alpha=None) -> None:
        """r = where the panel is on the screen now, in screen pixels. alpha: forced opacity while flying in."""
        self.pnl = pnl
        w, h = int(r.width() + 2 * PAD), int(r.height() + 2 * PAD)
        resized = (w, h) != (self.width(), self.height())
        if resized:
            self.setFixedSize(w, h)
        self.move(int(r.x() - PAD), int(r.y() - PAD))
        self.rect = QRectF(PAD + (r.x() - int(r.x())), PAD + (r.y() - int(r.y())), r.width(), r.height())
        look = (state, pnl["ver"], pnl["hover_tile"], pnl["editing"], pnl["title"], round(pnl["z"], 2),
                sys_state if pnl["kind"] == "system" else None)
        sec = time.localtime().tm_sec if pnl["kind"] == "clock" else None
        spin = pnl["kind"] == "model" and now - self._painted_at >= 0.05      # 3D models turn at 20 fps
        before = self.opacity
        self.opacity = min(1.0, self.opacity + 0.12) if alpha is None else alpha
        if resized or look != self.look or sec != self._sec or spin or self.opacity != before:
            self.look, self._sec, self._painted_at = look, sec, now
            self.update()
        if not self.isVisible():
            self.show()
        self.set_typing(bool(pnl["editing"]))

    def set_typing(self, on: bool) -> None:
        """A note being typed into takes the keyboard; otherwise the panel never takes focus or clicks."""
        if on == self.typing:
            return
        self.typing = on
        self.setWindowFlag(Qt.WindowTransparentForInput, not on)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, not on)
        self.show()
        if on:
            self.activateWindow()
            self.setFocus()

    def keyPressEvent(self, e):
        on_key = self.layer.on_key
        if on_key is None or not self.typing:
            return super().keyPressEvent(e)
        if e.key() == Qt.Key_Backspace:
            on_key("\b")
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            on_key("\n")
        elif e.text() and e.text().isprintable():
            on_key(e.text())

    def inputMethodEvent(self, e):                     # Thai and other input methods
        if self.layer.on_key is not None and self.typing and e.commitString():
            self.layer.on_key(e.commitString())

    def paintEvent(self, _):
        pnl = self.pnl
        if pnl is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setOpacity(self.opacity)
        r, z = self.rect, pnl["z"]
        state = self.look[0] if self.look else ""
        key = (round(r.width()), round(r.height()), state, round(z, 2), pnl["title"], pnl["kind"])
        if self._chrome is None or self._chrome[0] != key:            # the glass, made once per size and look
            dpr = self.devicePixelRatioF()
            img = QImage(int(self.width() * dpr) + 2, int(self.height() * dpr) + 2, QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.Antialiasing)
            q.setRenderHint(QPainter.TextAntialiasing)
            paint_glass(q, QRectF(PAD, PAD, r.width(), r.height()), pnl, z, state)
            q.end()
            self._chrome = (key, img)
        p.drawImage(QPointF(r.x() - PAD, r.y() - PAD), self._chrome[1])
        depth = 1.0 - 0.6 * z
        c = _content(r)
        kind = pnl["kind"]
        if kind == "clock":
            paint_clock(p, c, depth)
        elif kind == "system":
            paint_system(p, c, depth, self.layer.sys or {})
        elif kind == "note":
            paint_note(p, c, depth, pnl["data"].get("text", ""), pnl["editing"])
        elif kind == "image":
            paint_image(p, c, depth, pnl["data"].get("path"))
        elif kind == "model":
            paint_model(p, c, depth, pnl["data"].get("ref"), state)
        elif kind == "music":
            paint_music(p, c, depth)
        elif kind == "file":
            paint_file(p, c, depth, pnl["title"])
        if pnl.get("tiles"):
            x, y, w, h = pnl["rect"]
            sx, sy = r.width() / max(w, 1e-9), r.height() / max(h, 1e-9)
            for i, (rect, label, act) in enumerate(pnl["tiles"]):
                tx, ty, tw, th = rect
                t = QRectF(r.x() + (tx - x) * sx, r.y() + (ty - y) * sy, tw * sx, th * sy)
                paint_tile(p, t, depth, label, act, kind, pnl["hover_tile"] == i)
        p.end()


class StatusBar(Overlay):
    """The strip along the top of an OS: name, time, Wi-Fi, battery, volume."""
    H = 30

    def __init__(self):
        super().__init__(100, self.H)
        self.sys = {}
        self.look = None

    def place(self, sys_state: dict, W: int, x0: int, y0: int) -> None:
        if self.width() != W:
            self.setFixedSize(W, self.H)
            self.move(x0, y0)
        look = (time.strftime("%H:%M"), repr(sorted((sys_state or {}).items(), key=lambda kv: kv[0])))
        self.sys = sys_state or {}
        self.opacity = 1.0
        if look != self.look:
            self.look = look
            self.update()
        if not self.isVisible():
            self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        g = QLinearGradient(QPointF(0, 0), QPointF(0, H))
        g.setColorAt(0.0, _color((4, 16, 30), 235))
        g.setColorAt(1.0, _color((4, 16, 30), 150))
        p.fillRect(QRectF(0, 0, W, H), g)
        p.setPen(QPen(_color(GLASS, 90), 1))
        p.drawLine(QPointF(0, H - 0.5), QPointF(W, H - 0.5))
        p.setPen(_color(GLOW, 230))
        p.setFont(_font(8.5, True, 3))
        p.drawText(QRectF(16, 0, 300, H), Qt.AlignVCenter | Qt.AlignLeft, "SENSOR-OS")
        p.setPen(_color(WHITE, 240))
        p.setFont(_font(10, True))
        p.drawText(QRectF(0, 0, W, H), Qt.AlignCenter, time.strftime("%H:%M   ·   %a %d %b").upper())
        parts = []
        s = self.sys
        wifi = s.get("wifi")
        if wifi:
            name, level = wifi
            bars = "▂▄▆█"[:max(1, round((level or 0.5) * 4))] if level is not None else "▂▄▆"
            parts.append(f"{bars}  {name}")
        else:
            parts.append(tr("No Wi-Fi"))
        if s.get("volume") is not None:
            parts.append(tr("Muted") if s.get("muted") else f"♪ {round(s['volume'] * 100)}%")
        if s.get("battery") is not None:
            parts.append(("⚡ " if s.get("plugged") else "▮ ") + f"{round(s['battery'] * 100)}%")
        p.setPen(_color(ICE, 230))
        p.setFont(_font(9))
        p.drawText(QRectF(0, 0, W - 16, H), Qt.AlignVCenter | Qt.AlignRight, "     ".join(parts))
        p.end()


class ShelfView(Overlay):
    """The tabs along the bottom: panels put away, and "+ new panel"."""

    def __init__(self):
        super().__init__(100, 60)
        self.tabs = []
        self.look = None

    def place(self, tabs: list, W: int, H: int) -> None:
        if not tabs:
            if self.isVisible():
                self.hide()
            return
        x0 = min(t["rect"][0] for t in tabs) * W - 8
        x1 = max(t["rect"][0] + t["rect"][2] for t in tabs) * W + 8
        y0 = min(t["rect"][1] for t in tabs) * H - 8
        y1 = max(t["rect"][1] + t["rect"][3] for t in tabs) * H + 8
        if (int(x1 - x0), int(y1 - y0)) != (self.width(), self.height()):
            self.setFixedSize(int(x1 - x0), int(y1 - y0))
        self.move(int(x0), int(y0))
        self.origin = (x0, y0, W, H)
        look = [(t["id"], t["title"], t["state"]) for t in tabs]
        self.tabs = tabs
        if look != self.look:
            self.look = look
            self.update()
        if not self.isVisible():
            self.show()

    def paintEvent(self, _):
        if not self.tabs:
            return
        x0, y0, W, H = self.origin
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for tab in self.tabs:
            x, y, w, h = tab["rect"]
            r = QRectF(x * W - x0, y * H - y0, w * W, h * H)
            hover = tab["state"] == "hover"
            if tab["kind"] == "new":
                p.setPen(QPen(_color(ORANGE, 220), 1.6, Qt.DashLine))
                p.setBrush(_color((40, 24, 6), 200))
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
                p.setPen(_color(ORANGE, 245))
                p.setFont(_font(9, True))
                p.drawText(r, Qt.AlignCenter, "+  " + tr("New panel"))
                continue
            p.setPen(QPen(_color(GLASS, 230 if hover else 140), 1.4))
            p.setBrush(_color((10, 34, 58), 225))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            p.setPen(_color(WHITE, 235))
            p.setFont(_font(8.5, hover))
            name = tab["title"] or KIND_NAMES.get(tab["kind"], "")
            p.drawText(r.adjusted(12, 0, -12, 0), Qt.AlignCenter,
                       QFontMetrics(p.font()).elidedText(name, Qt.ElideRight, int(r.width() - 24)))
        p.end()


class DraftView(Overlay):
    """The outline of a new panel while two fists pull it out of thin air."""

    def __init__(self):
        super().__init__(100, 100)

    def place(self, rect, W: int, H: int) -> None:
        if rect is None:
            if self.isVisible():
                self.hide()
            return
        x, y, w, h = rect
        size = (max(int(w * W) + 24, 30), max(int(h * H) + 24, 30))
        if size != (self.width(), self.height()):
            self.setFixedSize(*size)
            self.update()
        self.move(int(x * W) - 12, int(y * H) - 12)
        self.opacity = 1.0
        if not self.isVisible():
            self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(12, 12, self.width() - 24, self.height() - 24)
        p.setPen(QPen(_color(ORANGE, 40), 8))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QPen(_color(ORANGE, 230), 2, Qt.DashLine))
        p.setBrush(_color((40, 110, 170), 50))
        p.drawRoundedRect(r, 10, 10)
        p.end()


FLY_IN, FLY_OUT, STAGGER = 0.55, 0.38, 0.07     # seconds: calling panels up / sending them away


def _ease_out_back(t: float) -> float:
    c = 1.70158
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class PanelLayer:
    """Keeps one PanelView per panel, plus the shelf and the outline of a panel being drawn.
    When all panels are called up at once they fly out of JARVIS (bottom centre) one after another,
    overshooting a little; sent away, they fly back into it and fade."""

    def __init__(self):
        self.views: dict = {}
        self.born: dict = {}          # panel id -> (start time, delay) while flying in
        self.leaving: dict = {}       # panel id -> (view, x, y, start time) while flying away
        self.shelf = ShelfView()
        self.draft = DraftView()
        self.bar = StatusBar()
        self.order: list = []
        self.sys = None
        self.on_key = None            # set by the HUD: keyboard text for the note being typed into

    def isVisible(self) -> bool:
        return False

    def raise_(self) -> None:
        pass

    def tick(self, s: Snapshot, dt: float) -> bool:
        """Returns True when it raised panel windows (so the hand rings can be raised above them again)."""
        sp = s.holo.get("panels") if (s.holo is not None and s.stage is None and s.enabled) else None
        if not sp:
            self.close_all()
            return False
        geo = QGuiApplication.primaryScreen().geometry()
        W, H = geo.width(), geo.height()
        now = time.perf_counter()
        age = max(0.0, min(now - sp["t"], LEAD))
        hx, hy = s.holo.get("head", (0.0, 0.0))
        par = sp.get("parallax", 0.0)
        self.sys = sp.get("sys")
        alive = []
        ox, oy = geo.x() + W / 2, geo.y() + H * 0.88        # where JARVIS's orb is
        burst = not self.views and len(sp["panels"]) > 1     # everything called up at once (not one new panel)
        for i, pnl in enumerate(reversed(sp["panels"]) if burst else []):     # front panels first
            if pnl["id"] not in self.born:
                self.born[pnl["id"]] = (now, i * STAGGER)
        for pnl in sp["panels"]:                         # back to front
            x, y, w, h = pnl["rect"]
            x += pnl["vx"] * age - hx * par * (1.0 - pnl["z"])
            y += pnl["vy"] * age - hy * par * (1.0 - pnl["z"])
            lift = 1.025 if pnl["state"] == "held" else 1.0
            cx, cy, rw, rh = (x + w / 2) * W + geo.x(), (y + h / 2) * H + geo.y(), w * W * lift, h * H * lift
            state = "edit" if pnl["editing"] else pnl["state"]
            alpha = None
            if pnl["id"] in self.born:                   # flying out of JARVIS to its place
                t0, delay = self.born[pnl["id"]]
                t = max(0.0, min(1.0, (now - t0 - delay) / FLY_IN))
                if t >= 1.0:
                    del self.born[pnl["id"]]
                else:
                    e = _ease_out_back(t)
                    cx, cy = ox + (cx - ox) * e, oy + (cy - oy) * e
                    alpha = min(1.0, t * 1.8)
            view = self.views.get(pnl["id"])
            if view is None:
                view = self.leaving.pop(pnl["id"], (None,))[0] or PanelView(self)
                self.views[pnl["id"]] = view
                if alpha is not None:
                    view.opacity = 0.0
            view.place(pnl, QRectF(cx - rw / 2, cy - rh / 2, rw, rh), state, now, self.sys, alpha)
            alive.append(pnl["id"])
        everything_away = not sp["panels"]
        for pid in [pid for pid in self.views if pid not in alive]:
            view = self.views.pop(pid)
            self.born.pop(pid, None)
            if everything_away and view.isVisible():     # sent away: fly back into JARVIS
                c = view.geometry().center()
                self.leaving[pid] = (view, c.x(), c.y(), now + len(self.leaving) * STAGGER * 0.5)
            else:
                view.close()
        for pid, (view, x, y, t0) in list(self.leaving.items()):
            t = max(0.0, (now - t0) / FLY_OUT)
            if t >= 1.0:
                view.close()
                del self.leaving[pid]
                continue
            e = t * t * t
            view.drift(x + (ox - x) * e - view.width() / 2 + PAD, y + (oy - y) * e - view.height() / 2 + PAD,
                       1.0 - t)
        self.shelf.place(sp["shelf"], W, H)
        if sp.get("statusbar"):
            self.bar.place(self.sys, W, geo.x(), geo.y())
        elif self.bar.isVisible():
            self.bar.hide()
        self.draft.place(sp.get("draft"), W, H)
        raised = False
        if alive != self.order:                          # stack the windows back to front
            for pid in alive:
                self.views[pid].raise_()
            self.order = alive
            raised = True
        return raised

    def close_all(self) -> None:
        for view in list(self.views.values()) + [v[0] for v in self.leaving.values()]:
            view.close()
        self.views.clear()
        self.leaving.clear()
        self.born.clear()
        self.order = []
        self.shelf.hide()
        self.draft.hide()
        self.bar.hide()

    def close(self) -> None:
        self.close_all()
        self.shelf.close()
        self.draft.close()
        self.bar.close()
