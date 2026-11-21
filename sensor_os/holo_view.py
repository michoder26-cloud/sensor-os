"""Hologram mode on screen.

HoloWallpaper  the mirrored camera image as a live wallpaper behind the desktop icons
HoloHandView   one glowing hand skeleton + ring, drawn exactly where the hand appears in the image
HoloFrame      glowing corners around the window the lead hand is over
"""
import time

import cv2
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import desktop
from .gestures import INDEX_TIP, THUMB_TIP
from .hud import CYAN, GREY, ORANGE, VIOLET, Overlay, _color, _font
from .preview import CONNECTIONS
from .shared import Snapshot
from .i18n import tr

TEAL = (90, 235, 215)
STATE_COLOR = {"press": ORANGE, "drag": ORANGE, "grab": ORANGE, "pull": ORANGE, "hub": TEAL, "held": VIOLET,
               "scale": VIOLET, "scroll": VIOLET, "fist": GREY,
               "prop_rot": ORANGE, "prop_move": ORANGE, "prop_scale": VIOLET}


class HoloWallpaper(QWidget):
    def __init__(self):
        # No click-through flag here: it makes the window "layered", and Windows does not draw
        # layered windows once they are children of the desktop host.
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.image = None
        self._frame = None
        self._attached = False
        self.s = Snapshot()

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        if s.holo is None or not (s.holo.get("camera") or s.holo.get("room")):
            if self.isVisible():
                self.hide()
                desktop.refresh_wallpaper()      # or the last camera frame stays on the desktop
            return
        room = not s.holo.get("camera")
        if room != getattr(self, "room", None):
            self.room = room
            self.update()
        if not self.isVisible():
            geo = QGuiApplication.primaryScreen().geometry()
            self.setGeometry(geo)
            self.show()
            if not self._attached:
                # Behind the desktop icons if Windows lets us; otherwise just below every other window.
                self._attached = desktop.attach_to_desktop(int(self.winId()))
            if not self._attached:
                self.lower()
        if self.room:
            return                                   # the glass room is painted once and stays
        if s.frame is not None and s.frame is not self._frame:
            self._frame = s.frame
            h, w = s.frame.shape[:2]
            u0, v0, u1, v1 = s.crop
            part = s.frame[int(v0 * h):int(v1 * h), int(u0 * w):int(u1 * w)]
            rgb = cv2.cvtColor(part, cv2.COLOR_BGR2RGB)
            self.image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy()
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        full = QRectF(0, 0, W, H)
        if getattr(self, "room", False):
            from .panel_view import backdrop
            dpr = self.devicePixelRatioF()
            if getattr(self, "_room", None) is None or self._room.width() != int(W * dpr):
                self._room = backdrop(W, H, dpr)
            p.drawImage(full, self._room)
            p.setPen(_color(TEAL, 150))
            p.setFont(_font(8, True, 3))
            p.drawText(QPointF(24, H - 60), "SENSOR-OS  ·  GLASS DESK")
            p.end()
            return
        if self.image is not None:
            p.drawImage(full, self.image)
        else:
            p.fillRect(full, QColor(3, 9, 18))
        tint = self.s.holo.get("tint", 0) if self.s.holo else 0
        if tint:
            p.fillRect(full, QColor(0, 10, 22, tint))            # the camera stays bright: you are the backdrop
        p.setPen(_color(TEAL, 170))
        p.setFont(_font(8, True, 3))
        p.drawText(QPointF(24, H - 60), "SENSOR-OS  ·  HOLOGRAM")
        p.end()


class HoloHandView(Overlay):
    SIZE = 620

    def __init__(self, index: int):
        super().__init__(self.SIZE, self.SIZE)
        self.index = index
        self.hand = None
        self.hold = 0.0
        self.origin = (0.0, 0.0)
        self.screen = (1920, 1080)

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        hands = s.holo["hands"] if (s.holo is not None and s.stage is None and s.enabled) else []
        self.hand = hands[self.index] if self.index < len(hands) else None
        self.hold = s.holo["hold"] if s.holo else 0.0
        if self.hand is not None:
            geo = QGuiApplication.primaryScreen().geometry()
            self.screen = (geo.width(), geo.height())
            pts = self.hand[0]
            x0, x1 = min(q[0] for q in pts) * geo.width(), max(q[0] for q in pts) * geo.width()
            y0, y1 = min(q[1] for q in pts) * geo.height(), max(q[1] for q in pts) * geo.height()
            # The window grows with the hand (a hand close to the camera is drawn large),
            # in 80 px steps so it is not resized on every frame.
            need = max(x1 - x0, y1 - y0) + 140
            size = int(min(max(geo.width(), geo.height()), max(320, -(-need // 80) * 80)))
            if size != self.width():
                self.setFixedSize(size, size)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            self.origin = (cx - size / 2, cy - size / 2)
            self.move(int(geo.x() + self.origin[0]), int(geo.y() + self.origin[1]))
        self.fade(1.0 if self.hand is not None else 0.0, dt, rate=16.0)

    def paintEvent(self, _):
        if self.hand is None:
            return
        pts, pos, state, lead = self.hand
        op = self.opacity * (1.0 if lead or state != "free" else 0.7)
        W, H = self.screen
        ox, oy = int(self.origin[0]), int(self.origin[1])
        q = [QPointF(x * W - ox, y * H - oy) for x, y in pts]
        rgb = STATE_COLOR.get(state, CYAN)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if self.s.holo and self.s.holo.get("skeleton"):
            # Skeleton: dark under-stroke, soft glow, bright core
            for width, color, alpha in ((9, (0, 8, 16), 110), (7, rgb, 45), (2.2, rgb, 235)):
                p.setPen(QPen(_color(color, alpha * op), width, Qt.SolidLine, Qt.RoundCap))
                for a, b in CONNECTIONS:
                    p.drawLine(q[a], q[b])
            p.setPen(Qt.NoPen)
            p.setBrush(_color((255, 255, 255), 235 * op))
            for point in q:
                p.drawEllipse(point, 2.6, 2.6)
        else:
            # Your real hand in the camera image is the hand; just mark the two fingertips that pinch.
            for tip in (q[THUMB_TIP], q[INDEX_TIP]):
                p.setPen(QPen(_color((0, 8, 16), 120 * op), 3))
                p.setBrush(_color(rgb, 235 * op))
                p.drawEllipse(tip, 5, 5)

        # Ring on the pinch point
        c = QPointF(pos[0] * W - ox, pos[1] * H - oy)
        apart = ((q[THUMB_TIP].x() - q[INDEX_TIP].x()) ** 2 + (q[THUMB_TIP].y() - q[INDEX_TIP].y()) ** 2) ** 0.5
        pressed = state in ("press", "drag", "held", "scale", "grab", "pull", "hub", "prop_rot", "prop_move", "prop_scale")
        radius = 12 if pressed else max(14.0, min(34.0, apart * 0.5))    # closes as the fingers approach
        p.setBrush(Qt.NoBrush)
        for width, color, alpha in ((6.5, (0, 8, 16), 120), (9, rgb, 50), (2.8, rgb, 250)):
            p.setPen(QPen(_color(color, alpha * op), width))
            p.drawEllipse(c, radius, radius)
        if pressed:
            p.setPen(Qt.NoPen)
            p.setBrush(_color(rgb, 150 * op))
            p.drawEllipse(c, 7, 7)
        elif state == "free" and lead:
            p.setPen(Qt.NoPen)
            p.setBrush(_color((255, 255, 255), 230 * op))
            p.drawEllipse(c, 2.5, 2.5)
        if state == "press" and self.hold > 0:
            pen = QPen(_color(VIOLET, 255 * op), 4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(c.x() - 26, c.y() - 26, 52, 52), 90 * 16, int(-360 * 16 * self.hold))
        p.end()


class HoloFrame(Overlay):
    """Glowing corner brackets around the window under the lead hand."""
    PAD = 10

    def __init__(self):
        super().__init__(100, 100)
        self.rect_px = None
        self.rgb = CYAN

    def tick(self, s: Snapshot, dt: float) -> None:
        self.s = s
        target = None
        if s.holo is not None and s.stage is None and s.enabled:
            target = s.holo.get("target")
            states = [h[2] for h in s.holo["hands"] if h[3]]
            self.rgb = STATE_COLOR.get(states[0], CYAN) if states else CYAN
        if target is not None and target != self.rect_px:
            self.rect_px = target
            dpr = self.devicePixelRatioF() or 1.0
            l, t, r, b = (v / dpr for v in target)
            pad = self.PAD
            self.setFixedSize(max(40, int(r - l + 2 * pad)), max(40, int(b - t + 2 * pad)))
            self.move(int(l - pad), int(t - pad))
        self.fade(1.0 if target is not None else 0.0, dt, rate=10.0)

    def paintEvent(self, _):
        op = self.opacity
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pad = self.PAD
        r = QRectF(pad - 3, pad - 3, self.width() - 2 * pad + 6, self.height() - 2 * pad + 6)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_color(self.rgb, 70 * op), 1.2))
        p.drawRect(r)
        L = min(46.0, r.width() / 4, r.height() / 4)
        for width, alpha in ((7, 60), (2.6, 250)):
            pen = QPen(_color(self.rgb, alpha * op), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for x, y, dx, dy in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                                 (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
                p.drawLine(QPointF(x, y), QPointF(x + L * dx, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + L * dy))
        p.end()


class HubView(Overlay):
    """The ring menu: ring, orbs and the glass list."""
    MARGIN = 40

    def __init__(self):
        super().__init__(200, 200)
        self.hub = None
        self.head = (0.0, 0.0)
        self.origin = (0, 0)
        self.screen = (1536, 864)
        self.angle = 0.0

    def tick(self, s: Snapshot, dt: float) -> None:
        from .hub import R_ORB, R_RING
        self.s = s
        hub = s.holo.get("hub") if (s.holo is not None and s.stage is None and s.enabled) else None
        self.angle = (self.angle + dt * 25) % 360
        if hub is not None:
            self.hub = hub
            self.head = s.holo.get("head", (0.0, 0.0))
            geo = QGuiApplication.primaryScreen().geometry()
            W, H = geo.width(), geo.height()
            self.screen = (W, H)
            cx, cy = hub["pos"][0] * W, hub["pos"][1] * H
            box = [cx - R_RING * H, cy - R_RING * H, cx + R_RING * H, cy + R_RING * H]
            for _label, x, y, _a, _h in hub["orbs"]:
                r = R_ORB * H * 1.2
                box = [min(box[0], x * W - r), min(box[1], y * H - r), max(box[2], x * W + r), max(box[3], y * H + r)]
            if hub["panel"]:
                x, y, w, h = hub["panel"]["rect"]
                box = [min(box[0], x * W), min(box[1], y * H), max(box[2], (x + w) * W), max(box[3], (y + h) * H)]
            m = self.MARGIN
            size = (int(box[2] - box[0] + 2 * m), int(box[3] - box[1] + 2 * m))
            if size != (self.width(), self.height()):
                self.setFixedSize(*size)
            self.origin = (int(box[0] - m), int(box[1] - m))
            self.move(geo.x() + self.origin[0], geo.y() + self.origin[1])
        self.fade(1.0 if hub is not None else 0.0, dt, rate=10.0)

    def paintEvent(self, _):
        from .hub import HEAD_H, R_ORB, R_RING, ROW_H, ROWS
        hub = self.hub
        if hub is None:
            return
        op = self.opacity
        W, H = self.screen
        ox, oy = self.origin
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(hub["pos"][0] * W - ox, hub["pos"][1] * H - oy)
        R = R_RING * H
        open_ = hub["state"] != "closed"

        # Ring: soft glow, bright band, thin inner ring, slow rotating ticks
        p.setBrush(_color((6, 22, 30), (120 if open_ else 70) * op))
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, R, R)
        p.setBrush(Qt.NoBrush)
        for width, alpha in ((16, 40), (7, 235 if (open_ or hub["ring_hover"]) else 190)):
            p.setPen(QPen(_color(TEAL, alpha * op), width))
            p.drawEllipse(c, R - 4, R - 4)
        p.setPen(QPen(_color((255, 255, 255), 150 * op), 1.2))
        p.drawEllipse(c, R - 16, R - 16)
        pen = QPen(_color(TEAL, 200 * op), 2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for k in range(3):
            p.drawArc(QRectF(c.x() - R - 9 - self.head[0] * 9, c.y() - R - 9 - self.head[1] * 9, 2 * R + 18, 2 * R + 18),
                      int((self.angle + 120 * k) * 16), 30 * 16)
        p.setPen(_color((255, 255, 255), 240 * op))
        p.setFont(_font(8.5, True, 3.5))
        p.drawText(QRectF(c.x() - R, c.y() - 15, 2 * R, 18), Qt.AlignCenter, "SENSOR")
        p.setPen(_color(TEAL, 230 * op))
        p.setFont(_font(6.5, True, 2.5))
        p.drawText(QRectF(c.x() - R, c.y() + 5, 2 * R, 14), Qt.AlignCenter, "ACTIVE" if open_ else "TAP")

        # Orbs
        for label, x, y, active, hover in hub["orbs"]:
            o = QPointF(x * W - ox, y * H - oy)
            r = R_ORB * H
            p.setPen(QPen(_color(TEAL, (250 if (active or hover) else 150) * op), 2.4 if (active or hover) else 1.5))
            p.setBrush(_color((8, 34, 40), (215 if active else 165) * op))
            p.drawEllipse(o, r, r)
            p.setPen(_color((255, 255, 255), 245 * op))
            p.setFont(_font(10, True))
            p.drawText(QRectF(o.x() - r, o.y() - 11, 2 * r, 22), Qt.AlignCenter, label)

        # Glass list
        panel = hub["panel"]
        if panel:
            x, y, w, h = panel["rect"]
            r = QRectF(x * W - ox, y * H - oy, w * W, h * H)
            p.setPen(QPen(_color(TEAL, 170 * op), 1.4))
            p.setBrush(_color((10, 40, 46), 190 * op))
            p.drawRoundedRect(r, 10, 10)
            head = HEAD_H * H
            p.setPen(_color(TEAL, 250 * op))
            p.setFont(_font(9, True, 2.5))
            p.drawText(QRectF(r.x() + 18, r.y(), r.width() - 36, head), Qt.AlignVCenter | Qt.AlignLeft, "▸  " + panel["title"])
            p.setPen(_color((255, 255, 255), 130 * op))
            p.setFont(_font(8))
            last = min(panel["first"] + ROWS, panel["total"])
            count = f"{panel['first'] + 1}–{last} / {panel['total']}" if panel["total"] else tr("empty")
            p.drawText(QRectF(r.x() + 18, r.y(), r.width() - 36, head), Qt.AlignVCenter | Qt.AlignRight, count)
            p.setPen(QPen(_color(TEAL, 90 * op), 1))
            p.drawLine(QPointF(r.x() + 12, r.y() + head), QPointF(r.right() - 12, r.y() + head))
            rh = ROW_H * H
            fm_font = _font(11.5)
            p.setFont(fm_font)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(fm_font)
            for k, (label, kind, hover) in enumerate(panel["rows"]):
                row = QRectF(r.x() + 8, r.y() + head + k * rh + 3, r.width() - 16, rh - 6)
                if hover:
                    p.setPen(QPen(_color(TEAL, 230 * op), 1.4))
                    p.setBrush(_color(TEAL, 55 * op))
                    p.drawRoundedRect(row, 6, 6)
                p.setPen(_color(TEAL if kind in ("dir", "back") else (255, 255, 255), 245 * op))
                p.drawText(row.adjusted(12, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(label, Qt.ElideRight, int(row.width() - 24)))
        p.end()


def draw_mesh(p, Vt, F, centre, radius: float, rgb, op: float, edge_rgb, levels: int = 12) -> None:
    """A mesh already turned (Vt = vertices in view space) as a translucent glowing hologram.
    Faces seen edge-on glow brightest: that rim light is what reads as a hologram."""
    import numpy as np
    from PySide6.QtGui import QBrush
    z = Vt[:, 2]
    k = 3.2 / (3.2 - z)                               # mild perspective: nearer = bigger
    P = Vt[:, :2] * k[:, None] * radius + np.asarray(centre)
    tri = Vt[F]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normal, axis=1)
    facing = np.abs(normal[:, 2]) / np.maximum(length, 1e-12)
    order = np.argsort(z[F].mean(axis=1))             # far faces first
    level = np.minimum((facing * levels).astype(int), levels - 1)
    brushes = [QBrush(_color(rgb, (26 + 78 * (1 - i / levels) ** 1.6) * op)) for i in range(levels)]
    p.setPen(QPen(_color(edge_rgb, 58 * op), 0.7))
    # Plain Python floats and one list walk: indexing numpy arrays face by face was most of the cost.
    tris = P[F][order].tolist()
    lv_list = level[order].tolist()
    current = None
    for (a, b, d), lv in zip(tris, lv_list):
        if lv != current:
            p.setBrush(brushes[lv])
            current = lv
        p.drawConvexPolygon([QPointF(a[0], a[1]), QPointF(b[0], b[1]), QPointF(d[0], d[1])])


class PropView(Overlay):
    """One floating 3D model, drawn as a translucent glowing hologram.

    Drawing it is the costliest thing on screen, so it is redrawn only when it looks different,
    at most FPS times a second while it merely spins or follows the head (full rate in the hand),
    and the gauge round a control is kept as a picture until its reading changes."""
    LEVELS = 12
    FPS = 20

    def __init__(self):
        super().__init__(200, 200)
        self.prop = None
        self.ref = None
        self.mesh = None
        self.failed = False
        self._last = None
        self._look = None
        self._painted_at = 0.0
        self._gauge = None
        self._gauge_key = None

    def show_prop(self, prop, W: int, H: int, gx: int, gy: int, dt: float, head=(0.0, 0.0), depth=(0.0, 0.0)) -> None:
        from . import models3d
        pid, ref, x, y, size, R, state, value, info = prop
        if ref != self.ref:
            self.ref, self.failed = ref, False
            try:
                self.mesh = models3d.load(ref)
            except Exception as exc:                     # unreadable file: show a marker, keep running
                print(f"Could not load 3D model {ref}: {exc}", flush=True)
                self.mesh, self.failed = None, True
        side = max(80, int(size * H * 1.3))
        if side != self.width():
            self.setFixedSize(side, side)
        self.move(int(gx + x * W - side / 2), int(gy + y * H - side / 2))
        self.head, self.depth = head, depth
        stamp = (size, state, R.tobytes(), round(head[0], 2), round(head[1], 2), value, info)
        look = (size, state, value, info)
        if stamp != self._last:
            now = time.monotonic()
            # A new reading, a touch or a resize shows at once; spinning and head motion at FPS.
            if look != self._look or state in ("held", "bin") or now - self._painted_at >= 1.0 / self.FPS:
                self.prop = prop
                self._last, self._look, self._painted_at = stamp, look, now
                self.update()
        elif self.prop is None:
            self.prop = prop
        # Not Overlay.fade(): that repaints every tick, and this is the one overlay too costly for it.
        before = self.opacity
        self.opacity += (1.0 - self.opacity) * min(1.0, dt * 8.0)
        if self.opacity > 0.995:
            self.opacity = 1.0
        if not self.isVisible():
            self.show()
        if abs(self.opacity - before) > 0.02 or (self.opacity == 1.0 and before < 1.0):
            self.update()

    def paintEvent(self, _):
        import numpy as np
        from PySide6.QtGui import QBrush, QPolygonF
        if self.prop is None:
            return
        _pid, ref, _x, _y, _size, R, state, value, info = self.prop
        op = self.opacity
        side = self.width()
        c = side / 2
        radius = side / 1.3 * 0.5 * 0.9
        rgb = ORANGE if state == "held" else (255, 80, 70) if state == "bin" else TEAL   # red: let go = thrown away
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        control = ref.split(":", 1)[1] if ref.startswith("control:") else None
        if control:
            key = (control, value, info, state, side, round(op, 2))
            if key != self._gauge_key:
                dpr = self.devicePixelRatioF()
                pm = QImage(int(side * dpr), int(side * dpr), QImage.Format_ARGB32_Premultiplied)
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.transparent)
                q = QPainter(pm)
                q.setRenderHint(QPainter.Antialiasing)
                q.setRenderHint(QPainter.TextAntialiasing)
                self._paint_control(q, control, value, info, state, rgb, c, radius, side, op)
                q.end()
                self._gauge, self._gauge_key = pm, key
            p.setRenderHint(QPainter.SmoothPixmapTransform)        # in case the screen scale changed since
            p.drawImage(0, 0, self._gauge)
            radius *= 0.80                                # the model sits inside its gauge
        else:
            # Projector ring under the model
            base = QRectF(c - radius * 0.75, c + radius * 0.92, radius * 1.5, radius * 0.22)
            for width, alpha in ((7, 35), (1.6, 190 if state else 120)):
                p.setPen(QPen(_color(rgb, alpha * op), width))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(base)

        if self.mesh is None:
            p.setPen(_color(rgb, 230 * op))
            p.setFont(_font(10, True))
            p.drawText(QRectF(0, 0, side, side), Qt.AlignCenter, tr("Cannot read model") if self.failed else "...")
            p.end()
            return

        V, F = self.mesh
        # Depth: the model sits in front of the screen. As the head moves, we see round its side
        # and it slides the opposite way, while the ring under it stays put on the screen.
        from .props import _rx, _ry
        hx, hy = getattr(self, "head", (0.0, 0.0))
        turn, shift = getattr(self, "depth", (0.0, 0.0))
        Vt = V @ (_ry(-hx * turn) @ _rx(hy * turn) @ np.asarray(R)).T
        c = np.array((c - hx * shift * side, c - hy * shift * side))
        draw_mesh(p, Vt, F, c, radius, rgb, op, (225, 255, 250) if state not in ("held", "bin") else (255, 235, 200), self.LEVELS)
        p.end()


def control_text(control: str, value, info) -> tuple:
    """(name, reading) shown on a control."""
    from . import models3d
    name = models3d.CONTROLS[control][0].upper()
    pct = lambda v: "–" if v is None else f"{round(v * 100)}%"
    if control == "volume":
        return name, tr("Muted") if info and info[0] else pct(value)
    if control == "brightness":
        return name, tr("not adjustable on this screen") if value is None else pct(value)
    if control == "media":
        return name, tr("tap = play/pause  ·  drag = skip")
    cpu, ram, battery, plugged = (tuple(info) + (None,) * 4)[:4]
    text = f"CPU {pct(cpu)}  ·  RAM {pct(ram)}"
    if battery is not None:
        text += f"  ·  {'⚡' if plugged else 'BAT'} {pct(battery)}"
    return name, text


def _paint_control(self, p, control, value, info, state, rgb, c, radius, side, op):
    """The gauge ring round a control and its name and reading."""
    muted = control == "volume" and bool(info and info[0])
    ring = QRectF(c - radius * 1.08, c - radius * 1.08, radius * 2.16, radius * 2.16)
    p.setBrush(Qt.NoBrush)
    pen = QPen(_color(rgb, (120 if state else 70) * op), 3)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawArc(ring, 225 * 16, -270 * 16)                  # the track: open at the bottom
    if value is not None and control != "media":
        hot = GREY if muted else (255, 120, 90) if control == "system" and value > 0.85 else rgb
        for width, alpha in ((11, 45), (4.5, 250)):
            pen = QPen(_color(hot, alpha * op), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 225 * 16, int(-270 * 16 * max(0.004, min(1.0, value))))
    name, reading = control_text(control, value, info)
    p.setPen(_color(rgb, 235 * op))
    p.setFont(_font(8, True, 2.5))
    p.drawText(QRectF(0, 0, side, c - radius * 1.12), Qt.AlignHCenter | Qt.AlignBottom, name)
    p.setPen(_color((255, 255, 255), 240 * op))
    p.setFont(_font(13 if len(reading) <= 6 else 8, len(reading) <= 6))
    p.drawText(QRectF(0, c + radius * 0.80, side, side - c - radius * 0.80), Qt.AlignHCenter | Qt.AlignVCenter, reading)


PropView._paint_control = _paint_control


class PropLayer:
    """Keeps one PropView window per floating model."""

    def __init__(self):
        self.views: dict[int, PropView] = {}

    def isVisible(self) -> bool:
        return False

    def raise_(self) -> None:
        pass

    def tick(self, s: Snapshot, dt: float) -> None:
        props = s.holo.get("props", []) if (s.holo is not None and s.stage is None and s.enabled) else []
        geo = QGuiApplication.primaryScreen().geometry()
        alive = set()
        for prop in props:
            alive.add(prop[0])
            view = self.views.get(prop[0])
            if view is None:
                view = self.views[prop[0]] = PropView()
            view.show_prop(prop, geo.width(), geo.height(), geo.x(), geo.y(), dt,
                           s.holo.get("head", (0.0, 0.0)), s.holo.get("depth", (0.0, 0.0)))
        for pid in [pid for pid in self.views if pid not in alive]:
            self.views.pop(pid).close()

    def close(self) -> None:
        for view in self.views.values():
            view.close()
        self.views.clear()
