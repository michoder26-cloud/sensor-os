"""What is drawn inside a glass panel that is not a live window: a clock and the machine's state.

Each painter gets the panel's content rectangle and an opacity, and draws with QPainter only.
"""
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPen

from .hud import ORANGE, _color, _font
from .i18n import tr

ICE = (150, 225, 255)
WHITE = (240, 250, 255)


def paint_clock(p, r: QRectF, op: float) -> None:
    now = time.localtime()
    p.setPen(_color(WHITE, 245 * op))
    p.setFont(_font(max(14.0, r.height() * 0.30), True))
    p.drawText(QRectF(r.x(), r.y() + r.height() * 0.08, r.width(), r.height() * 0.56),
               Qt.AlignCenter, time.strftime("%H:%M", now))
    p.setPen(_color(ICE, 210 * op))
    p.setFont(_font(max(7.0, r.height() * 0.085), False, 2.0))
    p.drawText(QRectF(r.x(), r.y() + r.height() * 0.64, r.width(), r.height() * 0.22),
               Qt.AlignCenter, time.strftime("%a  %d %b %Y", now).upper())
    # seconds as a thin sweeping line under the date
    k = now.tm_sec / 60.0
    y = r.y() + r.height() * 0.90
    x0, x1 = r.x() + r.width() * 0.18, r.x() + r.width() * 0.82
    p.setPen(QPen(_color(ICE, 50 * op), 2))
    p.drawLine(QPointF(x0, y), QPointF(x1, y))
    p.setPen(QPen(_color(ICE, 220 * op), 2))
    p.drawLine(QPointF(x0, y), QPointF(x0 + (x1 - x0) * k, y))


def system_rows(state: dict) -> list:
    """(label, level 0..1 or None, text) for the system panel."""
    pct = lambda v: "–" if v is None else f"{round(v * 100)}%"
    rows = [("CPU", state.get("cpu"), pct(state.get("cpu"))), ("RAM", state.get("ram"), pct(state.get("ram")))]
    if state.get("battery") is not None:
        rows.append(("BAT" + (" ⚡" if state.get("plugged") else ""), state["battery"], pct(state["battery"])))
    if state.get("volume") is not None:
        rows.append((tr("Volume").upper(), 0.0 if state.get("muted") else state["volume"],
                     tr("Muted") if state.get("muted") else pct(state["volume"])))
    return rows


def paint_system(p, r: QRectF, op: float, state: dict) -> None:
    rows = system_rows(state or {})
    if not rows:
        return
    pad = r.width() * 0.08
    row_h = r.height() / len(rows)
    for i, (label, level, text) in enumerate(rows):
        y = r.y() + i * row_h
        p.setPen(_color(ICE, 220 * op))
        p.setFont(_font(max(6.5, row_h * 0.30), True, 1.5))
        p.drawText(QRectF(r.x() + pad, y, r.width() * 0.35, row_h * 0.62), Qt.AlignLeft | Qt.AlignBottom, label)
        p.setPen(_color(WHITE, 235 * op))
        p.drawText(QRectF(r.x(), y, r.width() - pad, row_h * 0.62), Qt.AlignRight | Qt.AlignBottom, text)
        bar = QRectF(r.x() + pad, y + row_h * 0.70, r.width() - 2 * pad, max(2.0, row_h * 0.10))
        p.setPen(Qt.NoPen)
        p.setBrush(_color(ICE, 40 * op))
        p.drawRoundedRect(bar, 1.5, 1.5)
        if level is not None:
            hot = ORANGE if level > 0.85 and label in ("CPU", "RAM") else ICE
            p.setBrush(_color(hot, 220 * op))
            p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * max(0.0, min(1.0, level)), bar.height()), 1.5, 1.5)


# ---------------------------------------------------------------- panels you make
KIND_NAMES = {"clock": tr("Time"), "system": tr("System"), "note": tr("Note"), "image": tr("Picture"),
              "model": tr("3D models"), "music": tr("Music"), "folder": tr("Files"), "file": tr("Files"),
              "picker": tr("New panel: choose what goes in"), "window": "", "apps": tr("Apps"),
              "windows": tr("Open windows"), "power": tr("Power"), "ai": tr("JARVIS AI")}
CHOICE_GLYPH = {"note": "✎", "image": "▣", "model": "◈", "music": "♫", "folder": "▤", "clock": "◷", "system": "▦",
                "apps": "⊞", "windows": "❐", "power": "⏻", "ai": "✦"}
POWER_NAMES = {"lock": tr("Lock"), "sleep": tr("Sleep"), "restart": tr("Restart"), "shutdown": tr("Shut down"),
               "signout": tr("Sign out"), "exit": tr("Quit sensor-OS")}
POWER_GLYPH = {"lock": "⊠", "sleep": "☾", "restart": "↻", "shutdown": "⏻", "signout": "⇥", "exit": "✕"}
RED = (255, 110, 100)
GOLD = (255, 214, 120)


def paint_note(p, r: QRectF, op: float, text: str, editing: bool) -> None:
    pad = 10
    body = QRectF(r.x() + pad, r.y() + pad, r.width() - 2 * pad, r.height() - 2 * pad)
    if not text and not editing:
        p.setPen(_color(ICE, 150 * op))
        p.setFont(_font(10))
        p.drawText(body, Qt.AlignCenter | Qt.TextWordWrap, tr("Tap to type"))
        return
    p.setPen(_color(WHITE, 240 * op))
    p.setFont(_font(max(9.0, min(16.0, r.height() * 0.07))))
    p.drawText(body, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text + ("▌" if editing else ""))
    if editing:
        p.setPen(_color(GOLD, 200 * op))
        p.setFont(_font(7.5, True, 1.5))
        p.drawText(QRectF(r.x(), r.bottom() - 18, r.width() - 8, 16), Qt.AlignRight,
                   tr("Typing · tap again to finish"))


def paint_music(p, r: QRectF, op: float) -> None:
    top = QRectF(r.x(), r.y(), r.width(), r.height() * 0.45)
    p.setPen(_color(ICE, 230 * op))
    p.setFont(_font(max(14.0, top.height() * 0.42), True))
    p.drawText(top.adjusted(0, 0, 0, -top.height() * 0.25), Qt.AlignCenter, "♫")
    p.setPen(_color(ICE, 170 * op))
    p.setFont(_font(8, False, 1.5))
    p.drawText(QRectF(top.x(), top.bottom() - top.height() * 0.32, top.width(), top.height() * 0.3),
               Qt.AlignCenter, tr("Whatever is playing"))


def paint_file(p, r: QRectF, op: float, name: str) -> None:
    p.setPen(_color(ICE, 230 * op))
    p.setFont(_font(max(14.0, r.height() * 0.28), True))
    p.drawText(QRectF(r.x(), r.y(), r.width(), r.height() * 0.6), Qt.AlignCenter, "▤")
    p.setPen(_color(WHITE, 230 * op))
    p.setFont(_font(8.5))
    p.drawText(QRectF(r.x() + 4, r.y() + r.height() * 0.58, r.width() - 8, r.height() * 0.4),
               Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, name + "\n" + tr("Tap to open"))


def paint_picker_hint(p, r: QRectF, op: float) -> None:
    pass                      # the choices fill the panel; its title says what to do


def paint_tile(p, t: QRectF, op: float, label: str, act: str, kind: str, hover: bool) -> None:
    """One entry inside a panel: a choice in a new panel, a file in a folder, a music button."""
    p.setPen(QPen(_color(GOLD if hover else ICE, (230 if hover else 90) * op), 1.2))
    p.setBrush(_color((30, 90, 140) if hover else (10, 34, 58), (170 if hover else 120) * op))
    p.drawRoundedRect(t.adjusted(1, 1, -1, -1), 6, 6)
    if kind == "picker":
        glyph, name = CHOICE_GLYPH.get(label, "•"), KIND_NAMES.get(label, label)
    elif kind == "power":
        action = label.split(":", 1)[-1]
        if label.startswith("confirm:"):                 # waiting for the second tap: say what it will do
            p.setPen(QPen(_color(RED, 240 * op), 2))
            p.setBrush(_color((70, 16, 14), 200 * op))
            p.drawRoundedRect(t.adjusted(1, 1, -1, -1), 6, 6)
            p.setPen(_color(RED, 250 * op))
            p.setFont(_font(max(7.0, min(t.height() * 0.15, 10.0)), True))
            p.drawText(t.adjusted(4, 4, -4, -4), Qt.AlignCenter | Qt.TextWordWrap,
                       tr("Tap again\n= {}").format(POWER_NAMES.get(action, action)))
            return
        glyph, name = POWER_GLYPH.get(action, "•"), POWER_NAMES.get(action, action)
    elif kind == "apps" and act == "app":
        glyph, name = (label[:1].upper() or "•"), label
    elif kind == "apps" and act == "page":
        glyph, name = "»", label.replace("▸", "").strip()
    elif kind == "windows":
        glyph, name = "❐", label
    elif kind == "ai":
        on = label.startswith("✓ ")
        if on:                                            # the AI JARVIS uses now: lit up
            p.setPen(QPen(_color(GOLD, 240 * op), 2))
            p.setBrush(_color((60, 48, 14), 190 * op))
            p.drawRoundedRect(t.adjusted(1, 1, -1, -1), 6, 6)
        glyph, name = ("✓" if on else "✦"), label.replace("✓ ", "")
    elif kind == "music":
        glyph, name = label, ""
    else:
        low = label.lower()
        glyph = "⬑" if act == "up" else "▸" if act == "dir" else "▣" if low.endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")) \
            else "◈" if low.endswith((".obj", ".stl", ".glb", ".gltf", ".ply")) else "•"
        name = "" if act == "up" else label
    p.setPen(_color(GOLD if hover else ICE, 240 * op))
    if not name:
        p.setFont(_font(max(10.0, min(t.height() * 0.34, t.width() / (0.75 * max(len(glyph), 1)))), True))
        p.drawText(t, Qt.AlignCenter, glyph)
        return
    p.setFont(_font(max(9.0, min(t.height() * 0.32, 20.0)), True))
    p.drawText(QRectF(t.x(), t.y(), t.width(), t.height() * 0.58), Qt.AlignHCenter | Qt.AlignBottom, glyph)
    p.setPen(_color(WHITE, 230 * op))
    p.setFont(_font(max(6.5, min(t.height() * 0.16, 9.0))))
    from PySide6.QtGui import QFontMetrics
    p.drawText(QRectF(t.x() + 3, t.y() + t.height() * 0.60, t.width() - 6, t.height() * 0.38),
               Qt.AlignHCenter | Qt.AlignTop, QFontMetrics(p.font()).elidedText(name, Qt.ElideMiddle, int(t.width() - 8)))
