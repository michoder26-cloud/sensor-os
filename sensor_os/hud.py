"""JARVIS-style heads-up display.

Three small transparent, click-through, always-on-top windows:
    Reticle      glowing ring that follows the cursor and shows what the hand is doing
    StatusPanel  hand wireframe + status in the corner of the screen
    HelpCard     gesture cheat sheet / calibration prompts in the middle of the screen
"""
import math
import signal
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .preview import CONNECTIONS
from .shared import Shared, Snapshot
from .i18n import tr

CYAN = (70, 220, 255)
ORANGE = (255, 165, 60)
VIOLET = (195, 140, 255)
GREY = (150, 165, 180)
RED = (255, 100, 100)
PANEL_BG = (6, 16, 26)

MODE_COLOR = {"press": ORANGE, "drag": ORANGE, "held": ORANGE, "three": VIOLET, "scroll": VIOLET, "zoom": VIOLET, "grab": GREY, "wake": GREY}
MODE_TEXT = {
    "wake": tr("Hand found"),
    "move": tr("Move cursor"),
    "press": tr("Click"),
    "drag": tr("Drag"),
    "held": tr("Right click"),
    "grab": tr("Resting (fist)"),
    "three": tr("3 fingers: swipe to scroll"),
    "scroll": tr("Scroll"),
    "zoom": tr("Zoom"),
}
HELP_ROWS = (
    (tr("Open hand, move"), tr("Move cursor"), tr("Rest your elbow. Slow = precise, fast = far")),
    (tr("Pinch thumb + index"), tr("Click"), tr("Tap = click  ·  two taps = double click  ·  pinch and move = drag")),
    (tr("Pinch and hold still"), tr("Right click"), tr("Hold until the ring fills up")),
    (tr("3 fingers up, swipe"), tr("Scroll"), tr("Index + middle + ring (little finger folded)  ·  up/down or sideways")),
    (tr("Fist"), tr("Rest"), tr("Move your hand without anything happening")),
    (tr("Drag and flick"), tr("Throw a window"), tr("By its title bar: left/right = half screen  ·  up = maximize  ·  down = minimize")),
    (tr("Two fists"), tr("Zoom"), tr("Pull apart = zoom in  ·  push together = zoom out")),
    (tr("Raise both hands open"), tr("Window stage"), tr("Windows float to grab, move, resize, throw  ·  or Ctrl+Alt+S")),
)
HELP_FOOTER = tr("Ctrl+Alt+  Q on/off   C hand setup   M mode   S stage   H help   X quit")
HOLO_ROWS = (
    (tr("Open hand, move"), tr("Ring follows"), tr("The ring is where your hand is. Works with both hands")),
    (tr("Pinch = touch"), tr("Click / use a panel"), tr("On a program = click (hold = right click, move = drag)  ·  on a glass panel = use it (type, next picture, buttons)")),
    (tr("Fist = grab"), tr("Pick up, move, throw"), tr("Real windows and glass panels alike  ·  open = put down  ·  flick and open = throw")),
    (tr("Other hand grabs too"), tr("Stretch / shrink"), tr("Both hands on the same window or panel, pull apart or together")),
    (tr("Two fists on bare desktop, spread"), tr("New panel"), tr("Then pinch a choice: note, picture, 3D model, music, files, time, system  ·  or grab the + tab at the bottom")),
    (tr("Pinch a file in a files panel and pull"), tr("Pull it out"), tr("It comes out as its own panel  ·  fling a panel down off the screen = bin it")),
    (tr("3 fingers up, swipe"), tr("Scroll"), tr("Scrolls whatever is under that hand")),
    (tr("Clap"), tr("All panels up / away"), tr("Clap = every panel flies out of JARVIS  ·  clap again = all away, a clean screen")),
)
HOLO_COLORS = (CYAN, ORANGE, VIOLET, VIOLET, ORANGE, ORANGE, VIOLET, CYAN)
HOLO_FOOTER = tr("Ctrl+Alt+  J talk to JARVIS   A switch AI   G hide/show panels   Q on/off   C hand setup   M wrist mode   H help   X quit")
HELP_SECONDS = 14.0


def _color(rgb, alpha: float) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], max(0, min(255, int(alpha))))


def _font(size: float, bold: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(["Leelawadee UI", "Segoe UI", "Tahoma"])
    f.setPointSizeF(size)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


class Overlay(QWidget):
    """Frameless, translucent window that never takes focus or mouse input."""

    def __init__(self, width: int, height: int):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)     # macOS hides tool windows of inactive apps otherwise
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(width, height)
        self.s = Snapshot()
        self.opacity = 0.0

    def fade(self, target: float, dt: float, rate: float = 9.0) -> None:
        self.opacity += (target - self.opacity) * min(1.0, dt * rate)
        if self.opacity > 0.01:
            if not self.isVisible():
                self.show()
            self.update()
        elif self.isVisible():
            self.hide()


class Reticle(Overlay):
    SIZE = 180

    def __init__(self):
        super().__init__(self.SIZE, self.SIZE)
        self.angle = 0.0
        self.ripples = []            # [age, rgb]
        self._seen = None            # (click_seq, rclick_seq)

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        self.angle = (self.angle + dt * 70) % 360
        seq = (s.click_seq, s.rclick_seq)
        if self._seen is not None and seq != self._seen:
            self.ripples.append([0.0, ORANGE if seq[0] != self._seen[0] else VIOLET])
        self._seen = seq
        for r in self.ripples:
            r[0] += dt
        self.ripples = [r for r in self.ripples if r[0] < 0.45]

        if not s.enabled or s.mode == "lost" or s.stage is not None or s.holo is not None:
            target = 0.0
        elif s.mode in ("wake", "grab"):
            target = 0.45
        else:
            target = 1.0
        if self.ripples:
            target = max(target, 0.02)
        pos = QCursor.pos()
        self.move(pos.x() - self.SIZE // 2, pos.y() - self.SIZE // 2)
        self.fade(target, dt)

    def paintEvent(self, _):
        s, op = self.s, self.opacity
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.SIZE / 2
        rgb = MODE_COLOR.get(s.mode, CYAN)

        def ring(radius, width, alpha, color=rgb, start=None, span=None, shadow=False):
            rect = QRectF(c - radius, c - radius, 2 * radius, 2 * radius)
            p.setBrush(Qt.NoBrush)
            passes = [(_color(color, alpha), width)]
            if shadow:   # dark under-stroke keeps the ring readable on white windows
                passes.insert(0, (_color((0, 10, 20), alpha * 0.55), width + 2.6))
            for col, w in passes:
                pen = QPen(col, w)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                if start is None:
                    p.drawEllipse(rect)
                else:
                    p.drawArc(rect, int(start * 16), int(span * 16))

        for age, color in self.ripples:
            k = age / 0.45
            ring(14 + 62 * k, 3.0 * (1 - k) + 0.5, 230 * (1 - k), color)

        # Outer ring
        R = 40
        if s.mode == "drag":
            ring(R, 9, 55 * op)
            ring(R, 3.2, 240 * op, shadow=True)
        else:
            for k in range(3):
                a = self.angle + 120 * k
                ring(R, 8, 40 * op, start=a, span=72)
                ring(R, 2.4, 240 * op, start=a, span=72, shadow=True)
            for k in range(3):                                   # counter-rotating thin segments
                ring(R + 9, 1.2, 120 * op, start=-self.angle * 0.6 + 120 * k + 40, span=26)
        pen = QPen(_color(rgb, 150 * op), 1.4)
        p.setPen(pen)
        for k in range(4):                                       # tick marks
            a = math.radians(90 * k)
            p.drawLine(QPointF(c + math.cos(a) * (R + 16), c + math.sin(a) * (R + 16)),
                       QPointF(c + math.cos(a) * (R + 22), c + math.sin(a) * (R + 22)))

        # Inner ring closes in as the fingers approach a pinch
        if s.mode in ("press", "drag", "held", "three", "zoom"):
            p.setPen(Qt.NoPen)
            p.setBrush(_color(rgb, 70 * op))
            p.drawEllipse(QPointF(c, c), 15, 15)
            ring(10, 2.4, 255 * op, shadow=True)
            if s.mode == "press" and s.hold > 0:       # hold-for-right-click fills up clockwise
                ring(24, 4.0, 255 * op, VIOLET, start=90, span=-360 * s.hold, shadow=True)
            elif s.mode == "held":
                ring(24, 4.0, 255 * op, VIOLET, shadow=True)
        elif s.mode == "scroll":
            ring(10, 2.0, 220 * op, shadow=True)
            p.setPen(QPen(_color(rgb, 240 * op), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for sign in (-1, 1):
                path = QPainterPath()
                if s.scroll_axis == "y":
                    path.moveTo(c - 8, c + sign * 58)
                    path.lineTo(c, c + sign * 67)
                    path.lineTo(c + 8, c + sign * 58)
                else:
                    path.moveTo(c + sign * 58, c - 8)
                    path.lineTo(c + sign * 67, c)
                    path.lineTo(c + sign * 58, c + 8)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
        if s.summon > 0:
            ring(R + 16, 4.0, 255 * op, CYAN, start=90, span=-360 * s.summon, shadow=True)
        if s.mode == "move":
            inner = CYAN
            ring(30 - 20 * s.progress, 1.6 + 1.2 * s.progress, (110 + 145 * s.progress) * op, inner, shadow=True)
        p.end()


class StatusPanel(Overlay):
    W, H = 268, 124

    def __init__(self):
        super().__init__(self.W, self.H)
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.W - 18, geo.bottom() - self.H - 18)

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        self.fade(1.0, dt)

    def paintEvent(self, _):
        s, op = self.s, self.opacity
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not s.enabled:
            rgb, status = RED, tr("Control off")
        elif s.stage is not None:
            rgb, status = CYAN, tr("Window stage")
        elif s.holo is not None:
            rgb, status = CYAN, tr("Glass desk")
        elif s.mode == "lost":
            rgb, status = GREY, tr("No hand")
        else:
            rgb, status = MODE_COLOR.get(s.mode, CYAN), tr("Tracking")

        body = QRectF(1, 1, self.W - 2, self.H - 2)
        p.setPen(QPen(_color(rgb, 110 * op), 1.2))
        p.setBrush(_color(PANEL_BG, 185 * op))
        p.drawRoundedRect(body, 10, 10)
        p.setPen(QPen(_color(rgb, 230 * op), 2.2))            # corner brackets
        for x, y, dx, dy in ((6, 6, 1, 1), (self.W - 6, 6, -1, 1), (6, self.H - 6, 1, -1), (self.W - 6, self.H - 6, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 14 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 14 * dy))

        # Hand wireframe, fitted into its box
        box = QRectF(14, 14, 96, 96)
        p.setPen(QPen(_color(rgb, 60 * op), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 6, 6)
        if s.lm:
            xs, ys = [q[0] * 4 / 3 for q in s.lm], [q[1] for q in s.lm]
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
            k = (box.width() - 16) / span
            ox = box.center().x() - (min(xs) + max(xs)) / 2 * k
            oy = box.center().y() - (min(ys) + max(ys)) / 2 * k
            pts = [QPointF(ox + x * k, oy + y * k) for x, y in zip(xs, ys)]
            for width, alpha in ((5.0, 45), (1.6, 240)):
                p.setPen(QPen(_color(rgb, alpha * op), width, Qt.SolidLine, Qt.RoundCap))
                for a, b in CONNECTIONS:
                    p.drawLine(pts[a], pts[b])
            p.setPen(Qt.NoPen)
            p.setBrush(_color((255, 255, 255), 230 * op))
            for q in pts:
                p.drawEllipse(q, 1.6, 1.6)
        else:
            p.setPen(QPen(_color(rgb, 120 * op), 1.4, Qt.DashLine))
            p.drawEllipse(box.center(), 22, 22)

        x = 124
        p.setPen(_color(rgb, 210 * op))
        p.setFont(_font(8, True, 2.5))
        p.drawText(QPointF(x, 30), "SENSOR-OS" + ("  ·  TEST" if s.dry_run else ""))
        p.setPen(_color((255, 255, 255), 245 * op))
        p.setFont(_font(12.5, True))
        p.drawText(QPointF(x, 58), status)
        p.setPen(_color(rgb, 235 * op))
        p.setFont(_font(10.5))
        if not s.enabled:
            sub = tr("Ctrl+Alt+Q to turn on")
        elif s.stage is not None:
            sub = tr("Two fists to leave")
        elif s.holo is not None:
            n = len(s.holo["hands"])
            sub = tr("Raise a hand into view") if n == 0 else tr("{} hand(s) seen").format(n)
        else:
            sub = MODE_TEXT.get(s.mode, "")
        p.drawText(QPointF(x, 82), sub)
        low_light = 0 < s.fps < 22
        note = tr("  ·  low light, add light") if low_light else ""
        if s.glare:
            note = "  ·  " + tr("Glare on the {} of the camera picture: close the curtain / move the light").format(
                {"left": tr("left"), "right": tr("right"), "middle": tr("middle")}[s.glare])
        p.setPen(_color(ORANGE if (low_light or s.glare) else GREY, 220 * op))
        p.setFont(_font(8))
        p.drawText(QPointF(x, 104), f"{s.fps:.0f} fps" + note)
        p.end()


class HelpCard(Overlay):
    W, H = 700, 670

    def __init__(self):
        super().__init__(self.W, self.H)
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.W // 2, geo.center().y() - self.H // 2)
        self._help_seq = None
        self._help_left = 0.0

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        if self._help_seq is not None and s.help_seq != self._help_seq:
            self._help_left = 0.0 if self._help_left > 0 else HELP_SECONDS    # toggle
        self._help_seq = s.help_seq
        self._help_left = max(0.0, self._help_left - dt)
        self.fade(1.0 if (s.banner or self._help_left > 0) else 0.0, dt, rate=7.0)

    def paintEvent(self, _):
        s, op = self.s, self.opacity
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(_color(CYAN, 130 * op), 1.3))
        p.setBrush(_color(PANEL_BG, 225 * op))
        p.drawRoundedRect(QRectF(1, 1, self.W - 2, self.H - 2), 14, 14)
        p.setPen(QPen(_color(CYAN, 235 * op), 2.4))
        for x, y, dx, dy in ((8, 8, 1, 1), (self.W - 8, 8, -1, 1), (8, self.H - 8, 1, -1), (self.W - 8, self.H - 8, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 22 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 22 * dy))

        p.setPen(_color(CYAN, 220 * op))
        p.setFont(_font(9, True, 3))
        p.drawText(QPointF(36, 46), "SENSOR-OS")

        if s.banner:
            title, subtitle, progress = s.banner
            p.setPen(_color((255, 255, 255), 250 * op))
            p.setFont(_font(22, True))
            p.drawText(QRectF(36, 100, self.W - 72, 50), Qt.AlignCenter, title)
            p.setPen(_color(CYAN, 240 * op))
            p.setFont(_font(15))
            p.drawText(QRectF(36, 165, self.W - 72, 70), Qt.AlignCenter | Qt.TextWordWrap, subtitle)
            if progress is not None:
                bar = QRectF(120, 280, self.W - 240, 10)
                p.setPen(Qt.NoPen)
                p.setBrush(_color(CYAN, 50 * op))
                p.drawRoundedRect(bar, 5, 5)
                p.setBrush(_color(CYAN, 240 * op))
                p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * max(0.0, min(1.0, progress)), 10), 5, 5)
            p.end()
            return

        p.setPen(_color((255, 255, 255), 250 * op))
        p.setFont(_font(17, True))
        p.drawText(QPointF(36, 84), tr("Hand controls"))
        y = 122
        rows = HOLO_ROWS if s.holo is not None else HELP_ROWS
        colors = HOLO_COLORS if s.holo is not None else (CYAN, ORANGE, ORANGE, VIOLET, GREY, ORANGE, VIOLET, CYAN)
        for i, (gesture, effect, detail) in enumerate(rows):
            rgb = colors[i]
            p.setPen(Qt.NoPen)
            p.setBrush(_color(rgb, 235 * op))
            p.drawRoundedRect(QRectF(36, y - 15, 4, 40), 2, 2)
            p.setPen(_color(rgb, 250 * op))
            p.setFont(_font(13, True))
            p.drawText(QPointF(52, y), gesture)
            p.setPen(_color((255, 255, 255), 245 * op))
            p.setFont(_font(13, True))
            p.drawText(QPointF(250, y), effect)
            p.setPen(_color(GREY, 235 * op))
            p.setFont(_font(11))
            p.drawText(QPointF(52, y + 23), detail)
            y += 58
        p.setPen(_color(CYAN, 200 * op))
        p.setFont(_font(10))
        p.drawText(QRectF(36, self.H - 44, self.W - 72, 24), Qt.AlignCenter, HOLO_FOOTER if s.holo is not None else HELP_FOOTER)
        p.end()


JARVIS_TEXT = {"listening": tr("Listening…"), "thinking": tr("Thinking…"), "speaking": "", "error": ""}


class JarvisOrb(Overlay):
    """JARVIS at the bottom of the screen: a breathing orb (listening follows your voice, thinking
    spins, speaking pulses) with what it heard and what it answers. Hidden while it waits for its name."""
    W, H = 760, 150

    def __init__(self):
        super().__init__(self.W, self.H)
        geo = QGuiApplication.primaryScreen().geometry()
        self.move(geo.center().x() - self.W // 2, int(geo.bottom() - geo.height() * 0.12) - self.H)
        self.phase = 0.0
        self.level = 0.0
        self.act = 0.0                # 0 = waiting (a small orb in the middle), 1 = talking (orb + words)
        self._frames = 0

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        self.phase += dt
        j = s.jarvis or {}
        state = j.get("state", "off")
        self.level += (min(1.0, j.get("level", 0.0) / 3000.0) - self.level) * min(1.0, dt * 12)
        recent_error = state == "error" and time.time() - j.get("t", 0) < 10
        talking = state in ("listening", "thinking", "speaking") or recent_error
        before = self.act
        self.act += ((1.0 if talking else 0.0) - self.act) * min(1.0, dt * 7.0)
        self.opacity = 0.0 if state == "off" or not s.enabled else 1.0
        if self.opacity == 0.0:
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()
        self._frames += 1
        if self.act > 0.01 or abs(self.act - before) > 1e-3 or self._frames % 3 == 0:   # waiting: 20 fps is enough
            self.update()

    def paintEvent(self, _):
        j, act = self.s.jarvis or {}, self.act
        state = j.get("state", "off")
        rgb = {"thinking": VIOLET, "speaking": CYAN, "error": ORANGE}.get(state, CYAN)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.phase
        if act < 0.05:                                     # waiting for its name: just a quiet breathing orb
            c, r = QPointF(self.W / 2, self.H / 2), 11 + 1.5 * math.sin(t * 1.6)
            glow = 0.55 + 0.15 * math.sin(t * 1.6)
            for k in range(4, 0, -1):
                p.setPen(Qt.NoPen)
                p.setBrush(_color(CYAN, 14 * glow))
                p.drawEllipse(c, r + k * 5, r + k * 5)
            p.setBrush(_color(CYAN, 60 * glow))
            p.setPen(QPen(_color(CYAN, 200 * glow), 1.6))
            p.drawEllipse(c, r, r)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(_color((255, 255, 255), 150 * glow), 1.2, Qt.SolidLine, Qt.RoundCap))
            for i in range(3):
                p.drawArc(QRectF(c.x() - r - 6, c.y() - r - 6, 2 * r + 12, 2 * r + 12), int((t * 40 + i * 120) * 16), 60 * 16)
            p.end()
            return
        op = act
        p.setPen(QPen(_color(rgb, 90 * op), 1.2))
        p.setBrush(_color(PANEL_BG, 200 * op))
        box_w = 140 + (self.W - 142) * act                 # the box opens out from the orb
        p.drawRoundedRect(QRectF(self.W / 2 - (self.W / 2 - 1) * act, 1, box_w, self.H - 2), 18, 18)

        c = QPointF(self.W / 2 + (75 - self.W / 2) * act, self.H / 2)   # the orb slides left as it opens
        if state == "listening":
            r = 26 + 16 * self.level
        elif state == "speaking":
            r = 28 + 5 * math.sin(t * 9) + 3 * math.sin(t * 23)
        else:
            r = 28 + 2 * math.sin(t * 2)
        r = 11 + (r - 11) * act
        op = max(op, 0.55)
        for k in range(5, 0, -1):                          # glow
            p.setPen(Qt.NoPen)
            p.setBrush(_color(rgb, 16 * op))
            p.drawEllipse(c, r + k * 6, r + k * 6)
        p.setBrush(_color(rgb, 70 * op))
        p.setPen(QPen(_color(rgb, 240 * op), 2.2))
        p.drawEllipse(c, r, r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_color((255, 255, 255), 200 * op), 1.6, Qt.SolidLine, Qt.RoundCap))
        spin = t * (260 if state == "thinking" else 50)
        for i in range(3):                                  # the turning arcs
            p.drawArc(QRectF(c.x() - r - 10, c.y() - r - 10, 2 * r + 20, 2 * r + 20),
                      int((spin + i * 120) * 16), 70 * 16)

        x, w = 140, self.W - 160
        op = act * act                                     # the words come in last
        p.setPen(_color(rgb, 220 * op))
        p.setFont(_font(8.5, True, 3))
        p.drawText(QPointF(x, 34), "J.A.R.V.I.S." + (f"   ·   {j['brain'].upper()}" if j.get("brain") else ""))
        heard, reply = j.get("heard", ""), j.get("reply", "")
        if state == "error":
            line1, line2 = tr("Not available yet"), j.get("error", "")
        elif state == "speaking":
            line1, line2 = heard, reply
        else:
            line1, line2 = JARVIS_TEXT.get(state, ""), heard
        p.setPen(_color(GREY, 235 * op))
        p.setFont(_font(11))
        p.drawText(QRectF(x, 42, w, 26), Qt.AlignLeft | Qt.AlignVCenter, line1 if state != "speaking" else (("“" + line1 + "”") if line1 else ""))
        p.setPen(_color((255, 255, 255), 250 * op))
        p.setFont(_font(14, True))
        p.drawText(QRectF(x, 70, w, 70), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, line2)
        p.end()


def create_app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def run(app: QApplication, shared: Shared) -> None:
    """Blocks until the tracking thread finishes or Ctrl+C is pressed."""
    from .holo_view import HoloFrame, HoloHandView, HoloWallpaper, HubView, PropLayer
    from .panel_view import PanelLayer
    from .stage_view import StageHand, StageView
    stage = StageView()
    wallpaper = HoloWallpaper()
    panels = PanelLayer()
    panels.on_key = shared.push_key
    rings = [HoloHandView(0), HoloHandView(1)]
    hands = [StageHand(0), StageHand(1), HoloFrame(), PropLayer(), HubView(), panels] + rings
    widgets = [Reticle(), StatusPanel(), HelpCard(), JarvisOrb()]
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        dt, last[0] = min(now - last[0], 0.1), now
        s = shared.get()
        if s.finished:
            app.quit()
            return
        wallpaper.tick(s, dt)
        if stage.tick(s, dt):
            for w in hands + widgets:     # keep rings and HUD above the freshly opened stage
                if w.isVisible():
                    w.raise_()
        for w in hands + widgets:
            if w is panels:
                if panels.tick(s, dt):        # panel windows restacked: the hand rings go back on top
                    for r in rings:
                        if r.isVisible():
                            r.raise_()
            else:
                w.tick(s, dt)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(16)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    app.exec()
    timer.stop()
    for w in widgets + hands + [stage, wallpaper]:
        w.close()
