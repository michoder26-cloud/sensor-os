"""Stage mode on screen: the mirrored camera image full screen, real windows as live glass cards, hand rings.

Windows draws the live window thumbnails (DWM) on top of whatever this widget paints, so the
camera image, card frames and titles are painted here and the hand rings live in their own
small click-through windows above everything.
"""
import cv2
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import desktop
from .hud import CYAN, GREY, ORANGE, VIOLET, Overlay, _color, _font
from .shared import Snapshot
from .stage import TRAY_TOP
from .i18n import tr

HINT = tr("Tap = open   ·   pinch-drag = move   ·   two-hand pinch = resize   ·   flick = minimize   ·   two fists = leave")


class StageView(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setGeometry(QGuiApplication.primaryScreen().geometry())
        self.s = Snapshot()
        self.image = None
        self._frame = None
        self.thumbs: dict[int, desktop.Thumbnail] = {}
        self._order: list[int] = []

    # ---------------------------------------------------------------- lifecycle
    def tick(self, s: Snapshot, dt: float) -> bool:
        """Returns True on the tick the stage opens (so the hand rings can be raised above it)."""
        self.s = s
        if s.stage is None:
            if self.isVisible():
                self._drop_thumbs()
                self.hide()
            return False
        opened = False
        if not self.isVisible():
            self.setGeometry(QGuiApplication.primaryScreen().geometry())
            self.show()
            self.raise_()
            self.activateWindow()
            opened = True
        if s.frame is not None and s.frame is not self._frame:
            self._frame = s.frame
            self.image = self._to_image(s.frame, s.crop)
        self._sync_thumbs()
        self.update()
        return opened

    @staticmethod
    def _to_image(frame, crop):
        h, w = frame.shape[:2]
        u0, v0, u1, v1 = crop
        part = frame[int(v0 * h):int(v1 * h), int(u0 * w):int(u1 * w)]
        rgb = cv2.cvtColor(part, cv2.COLOR_BGR2RGB)
        return QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()

    # ---------------------------------------------------------------- thumbnails
    def _drop_thumbs(self):
        for th in self.thumbs.values():
            th.close()
        self.thumbs.clear()
        self._order = []

    def _sync_thumbs(self):
        cards = self.s.stage["cards"]
        order = [c[0] for c in reversed(cards)]            # back to front
        if order != self._order:
            # Thumbnails stack in the order they were registered, so rebuild when the order changes.
            self._drop_thumbs()
            hwnd = int(self.winId())
            for cid in order:
                self.thumbs[cid] = desktop.Thumbnail(hwnd, cid)
            self._order = order
        dpr = self.devicePixelRatioF()
        W, H = self.width() * dpr, self.height() * dpr
        for cid, _title, (x, y, w, h), state, source in cards:
            th = self.thumbs.get(cid)
            if th is not None:
                th.show((x * W, y * H, (x + w) * W, (y + h) * H), source, 255 if state != "held" else 235)

    # ---------------------------------------------------------------- painting
    def paintEvent(self, _):
        s = self.s
        if s.stage is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        full = QRectF(0, 0, W, H)
        if self.image is not None:
            p.drawImage(full, self.image)
        else:
            p.fillRect(full, QColor(4, 10, 18))
        p.fillRect(full, QColor(2, 12, 26, 95))

        # Desktop map outline
        p.setPen(QPen(_color(CYAN, 40), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(W * 0.1, H * 0.1, W * 0.8, H * 0.8))

        # Cards, back to front
        for cid, title, (x, y, w, h), state, _src in reversed(s.stage["cards"]):
            r = QRectF(x * W, y * H, w * W, h * H)
            rgb = ORANGE if state == "held" else CYAN
            glow = 255 if state else 120
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 22, 38, 235))
            p.drawRect(r)
            for width, alpha in ((10, 0.18), (4, 0.45), (1.6, 1.0)):
                p.setPen(QPen(_color(rgb, glow * alpha), width))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 6, 6)
            self._label(p, r, title, rgb, state)

        # Tray of minimized windows
        tray = s.stage["tray"]
        if tray:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(4, 14, 28, 200))
            p.drawRect(QRectF(0, TRAY_TOP * H, W, H - TRAY_TOP * H))
            p.setPen(_color(GREY, 220))
            p.setFont(_font(8, True, 2))
            p.drawText(QPointF(24, TRAY_TOP * H + 18), tr("Minimized"))
            for cid, title, (x, y, w, h), state in tray:
                r = QRectF(x * W, y * H, w * W, h * H)
                rgb = ORANGE if state == "held" else CYAN
                p.setPen(QPen(_color(rgb, 255 if state else 140), 1.6))
                p.setBrush(QColor(10, 28, 46, 240))
                p.drawRoundedRect(r, 6, 6)
                p.setPen(_color((255, 255, 255), 235))
                p.setFont(_font(9))
                p.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           QFontMetrics(p.font()).elidedText(title, Qt.ElideRight, int(r.width() - 16)))

        # Header
        p.setPen(_color(CYAN, 230))
        p.setFont(_font(9, True, 3))
        p.drawText(QPointF(28, 34), "SENSOR-OS  ·  STAGE" + ("  ·  TEST" if s.dry_run else ""))
        p.setPen(_color((255, 255, 255), 215))
        p.setFont(_font(11))
        p.drawText(QRectF(0, 18, W, 24), Qt.AlignHCenter, HINT)
        if not s.stage["hands"]:
            p.setPen(_color(CYAN, 230))
            p.setFont(_font(16, True))
            p.drawText(QRectF(0, H * 0.45, W, 40), Qt.AlignCenter, tr("Raise a hand into view"))
        p.end()

    @staticmethod
    def _label(p, r, title, rgb, state):
        p.setFont(_font(10, bool(state)))
        fm = QFontMetrics(p.font())
        text = fm.elidedText(title, Qt.ElideRight, int(max(60, r.width() - 10)))
        pill = QRectF(r.x() - 3, r.y() - fm.height() - 12, fm.horizontalAdvance(text) + 18, fm.height() + 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(4, 14, 28, 215))
        p.drawRoundedRect(pill, 4, 4)
        p.setPen(_color(rgb if state else (235, 245, 255), 250))
        p.drawText(pill, Qt.AlignCenter, text)
        # Fallback title in the middle, visible only when there is no live thumbnail
        p.setPen(_color(GREY, 200))
        p.drawText(r, Qt.AlignCenter, text)

    def closeEvent(self, e):
        self._drop_thumbs()
        super().closeEvent(e)


class StageHand(Overlay):
    """Ring drawn exactly on one hand's pinch point while the stage is open."""
    SIZE = 150

    def __init__(self, index: int):
        super().__init__(self.SIZE, self.SIZE)
        self.index = index
        self.hand = None
        self.exit = 0.0

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        hands = s.stage["hands"] if s.stage else []
        self.hand = hands[self.index] if self.index < len(hands) else None
        self.exit = s.stage["exit"] if s.stage else 0.0
        if self.hand is not None:
            geo = QGuiApplication.primaryScreen().geometry()
            x, y = self.hand[0] * geo.width() + geo.x(), self.hand[1] * geo.height() + geo.y()
            self.move(int(x - self.SIZE / 2), int(y - self.SIZE / 2))
        self.fade(1.0 if self.hand is not None else 0.0, dt, rate=14.0)

    def paintEvent(self, _):
        if self.hand is None and self.opacity < 0.02:
            return
        _, _, pinched, over, fist = self.hand if self.hand else (0, 0, False, False, False)
        op = self.opacity
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.SIZE / 2
        rgb = GREY if fist else ORANGE if pinched else CYAN
        radius = 13 if pinched else 24 if over else 20
        for width, alpha in ((9, 50), (2.6, 245)):
            pen = QPen(_color((0, 10, 20), alpha * 0.5 * op), width + 2.5)
            p.setPen(pen)
            p.drawEllipse(QPointF(c, c), radius, radius)
            p.setPen(QPen(_color(rgb, alpha * op), width))
            p.drawEllipse(QPointF(c, c), radius, radius)
        if pinched:
            p.setPen(Qt.NoPen)
            p.setBrush(_color(rgb, 120 * op))
            p.drawEllipse(QPointF(c, c), 8, 8)
        if self.exit > 0:
            pen = QPen(_color(VIOLET, 250 * op), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(c - 34, c - 34, 68, 68), 90 * 16, int(-360 * 16 * self.exit))
        p.end()
