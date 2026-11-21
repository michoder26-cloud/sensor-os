"""Render the example pictures in docs/images/ from sensor-OS's own drawing code, with made-up data.

    .venv\\Scripts\\python tools\\screenshots.py

Nothing from your PC is used (no real windows, files, mail or camera), so the pictures are safe to publish,
and they stay in step with how sensor-OS really looks. Nothing is shown on the screen while they are made.
"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt          # noqa: E402
from PySide6.QtGui import QImage, QPainter, QPen                  # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402

app = QApplication.instance() or QApplication([])

from sensor_os.config import Config                                # noqa: E402
from sensor_os.hud import CYAN, ORANGE, JarvisOrb, _color, _font   # noqa: E402
from sensor_os.panel_view import PanelView, ShelfView, StatusBar, backdrop  # noqa: E402
from sensor_os.space import Panel, SpaceEngine                     # noqa: E402
from sensor_os.stage import CameraMap                              # noqa: E402

W, H = 1920, 1080
OUT = ROOT / "docs" / "images"
SYSTEM = {"cpu": 0.23, "ram": 0.48, "battery": 0.81, "plugged": True, "volume": 0.45, "muted": False,
          "brightness": 0.70, "wifi": ("HomeNet", 0.8)}
APPS = [("Chrome", "a"), ("Spotify", "b"), ("VS Code", "c"), ("Terminal", "d"), ("Photos", "e"),
        ("Calculator", "f"), ("Notes", "g"), ("Settings", "h")]
FILES = [("Projects", "C:/Home/Projects", True), ("Photos", "C:/Home/Photos", True),
         ("Music", "C:/Home/Music", True), ("budget.xlsx", "C:/Home/budget.xlsx", False),
         ("trip-plan.md", "C:/Home/trip-plan.md", False), ("robot.glb", "C:/Home/robot.glb", False)]


def provide(kind, arg):
    return {"apps": APPS, "windows": [("Browser", 1, False), ("Music player", 2, False), ("Editor", 3, True)],
            "folder": FILES, "models": [("Torus knot", "builtin:knot")], "home": "C:/Home",
            "images": []}.get(kind, [])


def engine(panels):
    e = SpaceEngine(Config(), CameraMap(4 / 3, W / H, 0.8), W / H, provide=provide)
    e.panels = []
    for p in panels:
        e.add(p)
        if p.kind in ("apps", "folder", "windows", "ai", "power", "music"):
            e.fill(p, p.kind, "C:/Home" if p.kind == "folder" else None)
    return e


def layer():
    return SimpleNamespace(sys=SYSTEM, on_key=None)


def draw_panels(p, e, states=None):
    states = states or {}
    lay = layer()
    for pnl in e.view()["panels"]:
        x, y, w, h = pnl["rect"]
        r = QRectF(x * W, y * H, w * W, h * H)
        v = PanelView(lay)
        pad = 48
        v.setFixedSize(int(r.width() + 2 * pad), int(r.height() + 2 * pad))
        v.pnl, v.rect, v.opacity = pnl, QRectF(pad, pad, r.width(), r.height()), 1.0
        v.look = (states.get(pnl["id"], pnl["state"]),)
        v.render(p, QPoint(int(r.x() - pad), int(r.y() - pad)))


def draw_chrome(p, e):
    bar = StatusBar()
    bar.setFixedSize(W, bar.H)
    bar.sys = SYSTEM
    bar.render(p, QPoint(0, 0))
    tabs = e.view()["shelf"]
    if tabs:
        shelf = ShelfView()
        x0 = min(t["rect"][0] for t in tabs) * W - 8
        x1 = max(t["rect"][0] + t["rect"][2] for t in tabs) * W + 8
        y0 = min(t["rect"][1] for t in tabs) * H - 8
        y1 = max(t["rect"][1] + t["rect"][3] for t in tabs) * H + 8
        shelf.setFixedSize(int(x1 - x0), int(y1 - y0))
        shelf.origin, shelf.tabs = (x0, y0, W, H), tabs
        shelf.render(p, QPoint(int(x0), int(y0)))


def draw_orb(p, state="off", heard="", reply="", act=0.0):
    orb = JarvisOrb()
    orb.s = SimpleNamespace(jarvis={"state": state, "heard": heard, "reply": reply, "brain": "ChatGPT"}, enabled=True)
    orb.act, orb.phase, orb.level = act, 1.3, 0.4
    orb.render(p, QPoint(W // 2 - orb.W // 2, int(H * 0.88) - orb.H))


def draw_hand(p, x, y, rgb=CYAN, label=""):
    """A pointer ring where a hand would be (the real one follows your hand)."""
    c = QPointF(x * W, y * H)
    for k, a in ((26, 25), (18, 60)):
        p.setPen(QPen(_color(rgb, a), 6))
        p.drawEllipse(c, k, k)
    p.setPen(QPen(_color(rgb, 240), 2.4))
    p.drawEllipse(c, 12, 12)
    if label:
        p.setFont(_font(10, True, 1.5))
        p.setPen(_color(rgb, 235))
        p.drawText(QPointF(c.x() + 30, c.y() + 5), label)


def image():
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.drawImage(0, 0, backdrop(W, H, 1.0))
    return img, p


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    img.scaled(1600, 900, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(OUT / name), quality=92)
    print("wrote", OUT / name)


def desk():
    e = engine([
        Panel(1, "clock", "", 0.13, 0.17, 0.17, 0.15, z=0.25),
        Panel(2, "system", "", 0.87, 0.17, 0.17, 0.17, z=0.25),
        Panel(3, "apps", "", 0.30, 0.50, 0.26, 0.34, z=0.10),
        Panel(4, "folder", "", 0.63, 0.48, 0.24, 0.32, z=0.15),
        Panel(5, "music", "", 0.13, 0.72, 0.18, 0.15, z=0.40),
        Panel(6, "note", "", 0.86, 0.70, 0.17, 0.18, z=0.35, data={"text": "Buy milk\nCall Sam at 5\nShip v0.1 🚀"}),
        Panel(7, "model", "", 0.50, 0.18, 0.15, 0.20, z=0.55, data={"ref": "builtin:knot"}),
        Panel(8, "ai", "", 0.50, 0.80, 0.26, 0.15, z=0.05, stashed=True),
    ])
    e.ai_current = ("chatgpt", "")
    img, p = image()
    draw_panels(p, e, {3: "hover"})
    draw_chrome(p, e)
    draw_orb(p)
    draw_hand(p, 0.27, 0.53, CYAN, "open hand = point")
    p.end()
    save(img, "glass-desk.png")


def jarvis():
    e = engine([
        Panel(1, "clock", "", 0.13, 0.17, 0.17, 0.15, z=0.45),
        Panel(2, "system", "", 0.87, 0.17, 0.17, 0.17, z=0.45),
        Panel(4, "folder", "", 0.70, 0.45, 0.24, 0.32, z=0.6),
    ])
    img, p = image()
    draw_panels(p, e)
    draw_chrome(p, e)
    draw_orb(p, "speaking", "Jarvis, do I have new email?",
             "Three unread, sir. Two newsletters and one from the bank about your card.", act=1.0)
    p.end()
    save(img, "jarvis.png")


def throw():
    e = engine([
        Panel(1, "clock", "", 0.13, 0.17, 0.17, 0.15, z=0.3),
        Panel(3, "apps", "", 0.32, 0.50, 0.26, 0.34, z=0.2),
        Panel(6, "note", "", 0.97, 0.50, 0.20, 0.22, z=0.1, data={"text": "Old to-do list"}),
    ])
    img, p = image()
    draw_panels(p, e, {6: "bin"})
    draw_chrome(p, e)
    draw_hand(p, 0.965, 0.50, (255, 80, 70))
    p.setFont(_font(13, True, 1.5))
    p.setPen(_color((255, 120, 110), 240))
    p.drawText(QRectF(W * 0.55, H * 0.80, W * 0.42, 60), Qt.AlignRight,
               "drag a panel off the screen: red = let go to throw it away")
    arrow_y = H * 0.50
    p.setPen(QPen(_color((255, 120, 110), 200), 3, Qt.DashLine))
    p.drawLine(QPointF(W * 0.62, arrow_y), QPointF(W * 0.84, arrow_y))
    p.end()
    save(img, "throw-away.png")


if __name__ == "__main__":
    desk()
    jarvis()
    throw()
