"""The glass desk: real desktop and glass panels in one mode. Pinch = touch, fist = grab."""
import math

from sensor_os.config import Config
from sensor_os.desk import GlassDesk
from sensor_os.holo import HoloEngine
from sensor_os.space import Panel, SpaceEngine
from tests.test_holo import ASPECT, CMAP, DT, at

PANEL = (0.70, 0.40)          # a note panel sits here; everywhere else is a real window (or bare desktop)


class Sim:
    def __init__(self, windows_everywhere=True):
        self.cfg = Config()
        probe = (lambda x, y: True) if windows_everywhere else (lambda x, y: False)
        self.holo = HoloEngine(self.cfg, CMAP, ASPECT, probe=probe)
        self.space = SpaceEngine(self.cfg, CMAP, ASPECT)
        self.space.panels = [Panel(5, "note", "", *PANEL, 0.20, 0.20, z=0.0, data={"text": ""})]
        self.desk = GlassDesk(self.holo, self.space)
        self.t = 0.0
        self.hcmds, self.scmds = [], []

    def feed(self, hands, frames=1):
        for _ in range(frames):
            self.t += DT
            h, s = self.desk.update(hands, self.t)
            self.hcmds += h
            self.scmds += s

    def path(self, a, b, frames, **kw):
        for i in range(1, frames + 1):
            k = i / frames
            self.feed([at(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, **kw)])

    def holo_kinds(self):
        return [c[0] for c in self.hcmds if c[0] != "goto"]

    def space_kinds(self):
        return [c[0] for c in self.scmds]


def test_pinch_on_a_window_clicks_it():
    s = Sim()
    s.feed([at(0.3, 0.6)], 15)
    s.feed([at(0.3, 0.6, pinch=True)], 3)
    s.feed([at(0.3, 0.6)], 3)
    assert s.holo_kinds() == ["click"] and s.space_kinds() == []


def test_pinch_on_a_panel_uses_the_panel_and_clicks_nothing_behind_it():
    s = Sim()
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, pinch=True)], 3)
    s.feed([at(*PANEL)], 3)
    assert ("edit", 5) in s.scmds
    assert "click" not in s.holo_kinds() and "drag_start" not in s.holo_kinds()


def test_a_fist_on_a_panel_carries_the_panel_not_the_window_behind():
    s = Sim()
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, fist=True)], 3)
    s.path(PANEL, (0.45, 0.55), 15, fist=True)
    s.feed([at(0.45, 0.55, fist=True)], 15)
    p = s.space.panel(5)
    assert math.dist((p.x, p.y), (0.45, 0.55)) < 0.03
    assert "grab_start" not in s.holo_kinds() and "grab" in s.space_kinds()
    s.feed([at(0.45, 0.55)], 5)
    assert "drop" in s.space_kinds()


def test_a_fist_on_a_window_picks_up_the_window():
    s = Sim()
    s.feed([at(0.3, 0.6)], 15)
    s.feed([at(0.3, 0.6, fist=True)], 6)
    assert "grab_start" in s.holo_kinds() and s.space_kinds() == []


def test_two_fists_on_bare_desktop_pulled_apart_make_a_new_panel():
    s = Sim(windows_everywhere=False)
    a, b = (0.40, 0.55), (0.50, 0.65)
    s.feed([at(*a), at(*b)], 15)
    s.feed([at(*a, fist=True), at(*b, fist=True)], 2)
    for i in range(1, 11):
        k = i / 10
        s.feed([at(0.40 - 0.1 * k, 0.55 - 0.1 * k, fist=True), at(0.50 + 0.1 * k, 0.65 + 0.1 * k, fist=True)])
    s.feed([at(0.30, 0.45, fist=True), at(0.60, 0.75, fist=True)], 8)
    s.feed([at(0.30, 0.45), at(0.60, 0.75)], 3)
    assert "create" in s.space_kinds()
    assert [p.kind for p in s.space.panels].count("picker") == 1


def test_the_open_hand_still_moves_the_pointer_over_a_panel():
    s = Sim()
    s.feed([at(*PANEL)], 20)
    gotos = [c for c in s.hcmds if c[0] == "goto"]
    assert gotos and math.dist(gotos[-1][1:], PANEL) < 0.01
    assert 5 in s.space.hover


def test_hidden_panels_leave_the_hand_to_the_desktop():
    s = Sim()
    s.space.hidden = True
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, pinch=True)], 3)
    s.feed([at(*PANEL)], 3)
    assert s.holo_kinds() == ["click"] and s.space_kinds() == []
    assert s.space.view()["panels"] == []



def test_a_pinch_on_a_panel_then_moving_carries_the_panel_not_the_window_behind():
    s = Sim()
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, pinch=True)], 1)
    s.path(PANEL, (0.45, 0.60), 12, pinch=True)                 # pinch and pull, as on the desktop: a drag
    s.feed([at(0.45, 0.60, pinch=True)], 10)
    s.feed([at(0.45, 0.60)], 3)
    p = s.space.panel(5)
    assert abs(p.x - 0.45) < 0.05 and abs(p.y - 0.60) < 0.05        # the panel came along
    assert "click" not in s.holo_kinds() and "drag_start" not in s.holo_kinds()   # nothing behind it was touched


def test_a_fist_tap_on_a_panel_never_presses_its_buttons():
    s = Sim()
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, fist=True)], 3)
    s.feed([at(*PANEL)], 3)
    assert "press" not in s.space_kinds() or not any(k in ("power", "media", "open_file", "focus", "ai")
                                                      for k in s.space_kinds())


def test_after_a_press_on_a_panel_the_next_pinch_beside_it_clicks_the_window():
    s = Sim()
    s.feed([at(*PANEL)], 15)
    s.feed([at(*PANEL, pinch=True)], 3)
    s.feed([at(*PANEL)], 3)
    s.path(PANEL, (0.3, 0.6), 10)
    s.feed([at(0.3, 0.6)], 10)
    s.feed([at(0.3, 0.6, pinch=True)], 3)
    s.feed([at(0.3, 0.6)], 3)
    assert s.holo_kinds() == ["click"]
