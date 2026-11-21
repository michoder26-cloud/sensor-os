"""Glass space with synthetic hands: one gesture (grab) for everything."""
import math
from types import SimpleNamespace

from sensor_os.config import Config
from sensor_os.gestures import Hand
from sensor_os.space import CLOCK, SYSTEM, Panel, SpaceEngine, initial_panels, shelf_rects
from sensor_os.stage import CameraMap
from tests.test_gestures import FIST, OPEN, PINCH, hand
from tests.test_holo import claw_at

DT = 1 / 30
ASPECT = 16 / 9
CMAP = CameraMap(4 / 3, ASPECT, 0.8)
PALM = (0, 5, 9, 13, 17)


def palm_at(x, y, shape="open", scale=1.0):
    """A hand whose palm centre sits at screen fraction (x, y). scale > 1 = nearer the camera."""
    if shape == "claw":
        world = claw_at(0.5, 0.5).world
        base = hand(fingers=OPEN)
    else:
        base = {"open": hand(fingers=OPEN), "fist": hand(fingers=FIST), "pinch": hand(**PINCH)}[shape]
        world = base.world
    lm = base.lm
    wx, wy = lm[0]
    lm = [(wx + (u - wx) * scale, wy + (v - wy) * scale) for u, v in lm]
    cu = sum(lm[i][0] for i in PALM) / len(PALM)
    cv = sum(lm[i][1] for i in PALM) / len(PALM)
    tu, tv = CMAP.to_frame(x, y)
    return Hand(lm=[(u + tu - cu, v + tv - cv) for u, v in lm], world=world)


class Sim:
    def __init__(self, panels=None):
        self.cfg = Config()
        self.engine = SpaceEngine(self.cfg, CMAP, ASPECT)
        self.engine.panels = panels if panels is not None else [Panel(1, "window", "A", 0.5, 0.5, 0.3, 0.3, z=0.3)]
        self.t = 0.0
        self.cmds = []

    def feed(self, hands, frames=1):
        out = []
        for _ in range(frames):
            self.t += DT
            out += self.engine.update(hands, self.t)
        self.cmds += out
        return out

    def path(self, a, b, frames, shape="open", **kw):
        for i in range(1, frames + 1):
            k = i / frames
            self.feed([palm_at(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, shape, **kw)])

    def kinds(self):
        return [c[0] for c in self.cmds]

    def p(self, pid=1):
        return self.engine.panel(pid)


def test_any_closed_hand_grabs_on_the_first_frame():
    for shape in ("pinch", "fist", "claw"):
        s = Sim()
        s.feed([palm_at(0.5, 0.5)], 10)
        out = s.feed([palm_at(0.5, 0.5, shape)])
        assert out == [("grab", 1)], shape


def test_an_open_hand_only_hovers():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 20)
    assert s.kinds() == [] and s.engine.hover == {1}
    assert s.engine.view()["panels"][0]["state"] == "hover"


def test_a_hand_that_arrives_closed_grabs_nothing_until_it_opens():
    s = Sim()
    s.feed([palm_at(0.5, 0.5, "fist")], 10)
    assert s.kinds() == []
    s.feed([palm_at(0.5, 0.5)], 2)
    s.feed([palm_at(0.5, 0.5, "fist")])
    assert s.kinds() == ["grab"]


def test_the_panel_stays_under_the_hand_and_a_gentle_release_leaves_it_there():
    s = Sim()
    s.feed([palm_at(0.45, 0.48)], 10)
    s.feed([palm_at(0.45, 0.48, "fist")], 3)
    s.path((0.45, 0.48), (0.30, 0.40), 20, "fist")
    s.feed([palm_at(0.30, 0.40, "fist")], 20)
    p = s.p()
    assert math.dist((p.x, p.y), (0.35, 0.42)) < 0.01      # same offset from the hand as when grabbed
    s.feed([palm_at(0.30, 0.40)], 30)
    assert s.kinds() == ["grab", "drop"]
    assert math.dist((p.x, p.y), (0.35, 0.42)) < 0.02


def test_a_released_panel_slides_on_then_stops_inside_the_screen():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 2)
    s.path((0.5, 0.5), (0.62, 0.5), 6, "fist")             # about 1.1 screen heights/s
    s.feed([palm_at(0.62, 0.5)])
    x_release = s.p().x
    s.feed([palm_at(0.62, 0.5)], 90)
    p = s.p()
    assert p.x > x_release + 0.02 and p.vx == 0.0          # slid on, then came to rest
    x, y, w, h = s.engine.rect(p)
    assert 0 <= x and x + w <= 1 and not p.stashed


def test_a_hard_throw_sends_it_to_the_shelf_and_grabbing_the_tab_brings_it_back():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 2)
    s.path((0.5, 0.5), (0.9, 0.5), 4, "fist")
    s.feed([palm_at(0.95, 0.5)], 30)
    assert "stash" in s.kinds() and s.p().stashed           # a real window off the screen: to the shelf
    tab = shelf_rects(2)[0]                                  # the panel's tab, then the + tab
    c = (tab[0] + tab[2] / 2, tab[1] + tab[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "fist")])
    assert s.kinds()[-1] == "grab" and not s.p().stashed
    s.path(c, (0.5, 0.5), 15, "fist")
    s.feed([palm_at(0.5, 0.5, "fist")], 15)
    assert math.dist((s.p().x, s.p().y), (0.5, 0.5)) < 0.02


def test_pulling_the_hand_towards_the_camera_brings_the_panel_forward():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 5)
    z0 = s.p().z
    for i in range(1, 16):
        s.feed([palm_at(0.5, 0.5, "fist", scale=1 + 0.4 * i / 15)])
    s.feed([palm_at(0.5, 0.5, "fist", scale=1.4)], 30)
    near = s.p().z
    assert near < z0 - 0.15
    for i in range(1, 16):
        s.feed([palm_at(0.5, 0.5, "fist", scale=1.4 - 0.65 * i / 15)])
    s.feed([palm_at(0.5, 0.5, "fist", scale=0.75)], 30)
    assert s.p().z > z0 + 0.15
    s.feed([palm_at(0.5, 0.5)], 3)
    assert "open" not in s.kinds()                          # a depth change is not a tap


def test_two_hands_stretch_the_panel():
    s = Sim()
    a, b = (0.45, 0.5), (0.55, 0.5)
    s.feed([palm_at(*a), palm_at(*b)], 10)
    h0 = s.p().h
    s.feed([palm_at(*a, "fist"), palm_at(*b)], 3)
    s.feed([palm_at(*a, "fist"), palm_at(*b, "fist")], 3)
    for i in range(1, 11):
        k = i / 10
        s.feed([palm_at(a[0] - 0.05 * k, 0.5, "fist"), palm_at(b[0] + 0.05 * k, 0.5, "fist")])
    s.feed([palm_at(0.40, 0.5, "fist"), palm_at(0.60, 0.5, "fist")], 20)
    assert abs(s.p().h / h0 - 2.0) < 0.15
    assert abs(s.p().x - 0.5) < 0.02
    s.feed([palm_at(0.40, 0.5, "fist"), palm_at(0.60, 0.5)], 3)   # one lets go: the other carries on, no jump
    x = s.p().x
    s.feed([palm_at(0.40, 0.5, "fist"), palm_at(0.60, 0.5)], 5)
    assert abs(s.p().x - x) < 0.01 and "open" not in s.kinds()


def test_grab_and_let_go_at_once_opens_it():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "pinch")], 4)
    s.feed([palm_at(0.5, 0.5)], 2)
    assert s.kinds() == ["grab", "open"]
    assert (s.p().x, s.p().y) == (0.5, 0.5)


def test_a_hand_lost_while_holding_leaves_the_panel_where_it_was():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 2)
    s.path((0.5, 0.5), (0.7, 0.5), 4, "fist")
    s.feed([], 20)
    assert "throw" not in s.kinds() and not s.p().stashed and s.p().vx == 0.0


def test_the_front_panel_is_the_one_grabbed():
    back = Panel(1, "window", "back", 0.5, 0.5, 0.3, 0.3, z=0.7)
    front = Panel(2, "window", "front", 0.5, 0.5, 0.3, 0.3, z=0.1)
    s = Sim([back, front])
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")])
    assert s.kinds() == ["grab"] and s.cmds[0][1] == 2
    assert [p["id"] for p in s.engine.view()["panels"]] == [1, 2]   # drawn back to front


def test_first_layout_and_sync():
    wins = [SimpleNamespace(hwnd=10 + i, title=f"w{i}", rect=(0, 0, 1200, 800), source=None, minimized=i == 3)
            for i in range(4)]
    cfg = Config()
    panels = initial_panels(wins, (1920, 1080), cfg)
    ids = {p.id for p in panels}
    assert {CLOCK, SYSTEM, 10, 11, 12, 13} == ids
    assert next(p for p in panels if p.id == 13).stashed
    for p in panels:
        if not p.stashed:
            assert 0 < p.x < 1 and 0 < p.y < 1 and p.w <= 0.36
    s = Sim(panels)
    s.engine.panel(10).x = 0.2
    s.engine.sync(initial_panels(wins[:2], (1920, 1080), cfg))
    assert s.engine.panel(10).x == 0.2 and s.engine.panel(12) is None


def test_small_hand_wobbles_do_not_move_the_panel_in_depth():
    s = Sim()
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 5)
    for k in (1.05, 0.95, 1.07, 0.94, 1.0):
        s.feed([palm_at(0.5, 0.5, "fist", scale=k)], 6)
    assert s.p().z == 0.3


def test_a_near_miss_still_catches_the_panel():
    s = Sim()                                               # panel spans x 0.35-0.65 at z 0.3 (seen smaller)
    x, y, w, h = s.engine.rect(s.p())
    s.feed([palm_at(x + w + 0.015, 0.5)], 10)
    s.feed([palm_at(x + w + 0.015, 0.5, "fist")])
    assert s.kinds() == ["grab"]
    s2 = Sim()
    s2.feed([palm_at(x + w + 0.08, 0.5)], 10)
    s2.feed([palm_at(x + w + 0.08, 0.5, "fist")])
    assert s2.kinds() == []


# ---------------------------------------------------------------- making things: phase 2
FILES = {
    "C:/Pics": [("a.jpg", "C:/Pics/a.jpg", False), ("b.png", "C:/Pics/b.png", False)],
    "C:/Home": [("Pics", "C:/Pics", True), ("cube.obj", "C:/Home/cube.obj", False), ("notes.txt", "C:/Home/notes.txt", False)],
}


def provide(kind, arg):
    if kind == "folder":
        return FILES.get(arg, [])
    if kind == "images":
        return [path for _, path, d in FILES.get(arg, []) if not d]
    if kind == "models":
        return [("Knot", "builtin:knot"), ("Gem", "builtin:gem")]
    if kind == "home":
        return "C:/Home"
    if kind == "pictures":
        return "C:/Pics"
    return []


def make_sim(panels=()):
    s = Sim(list(panels))
    s.engine.provide = provide
    return s


def test_two_hands_pull_a_new_panel_out_of_thin_air_then_choose_what_it_is():
    s = make_sim()
    a, b = (0.45, 0.45), (0.55, 0.55)
    s.feed([palm_at(*a), palm_at(*b)], 10)
    s.feed([palm_at(*a, "fist"), palm_at(*b, "fist")], 2)
    for i in range(1, 11):
        k = i / 10
        s.feed([palm_at(0.45 - 0.1 * k, 0.45 - 0.1 * k, "fist"), palm_at(0.55 + 0.1 * k, 0.55 + 0.1 * k, "fist")])
    s.feed([palm_at(0.35, 0.35, "fist"), palm_at(0.65, 0.65, "fist")], 10)
    assert s.engine.view()["draft"] is not None
    s.feed([palm_at(0.35, 0.35), palm_at(0.65, 0.65)], 3)
    assert s.kinds() == ["create"]
    p = s.engine.panel(s.cmds[0][1])
    x, y, w, h = s.engine.rect(p)
    assert p.kind == "picker" and abs(x - 0.35) < 0.03 and abs(w - 0.30) < 0.04
    tiles = s.engine.tiles(p)
    note = next(r for r, label, act in tiles if act == ("make", "note"))
    c = (note[0] + note[2] / 2, note[1] + note[3] / 2)
    s.feed([palm_at(*c), palm_at(0.9, 0.9)], 10)
    s.feed([palm_at(*c, "pinch"), palm_at(0.9, 0.9)], 2)
    assert p.kind == "note" and "make" in s.kinds()


def test_hands_closing_near_each_other_do_not_make_a_tiny_panel():
    s = make_sim()
    s.feed([palm_at(0.48, 0.5), palm_at(0.52, 0.5)], 10)
    s.feed([palm_at(0.48, 0.5, "fist"), palm_at(0.52, 0.5, "fist")], 10)
    s.feed([palm_at(0.48, 0.5), palm_at(0.52, 0.5)], 3)
    assert "create" not in s.kinds()


def test_the_plus_tab_puts_a_new_panel_in_the_hand():
    s = make_sim()
    tab = shelf_rects(1)[0]
    c = (tab[0] + tab[2] / 2, tab[1] + tab[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "fist")])
    assert s.kinds() == ["create", "grab"]
    s.path(c, (0.5, 0.4), 15, "fist")
    s.feed([palm_at(0.5, 0.4, "fist")], 15)
    p = s.engine.panel(s.cmds[0][1])
    assert p.kind == "picker" and math.dist((p.x, p.y), (0.5, 0.4)) < 0.03


def test_a_note_takes_typing_after_a_tap():
    s = make_sim([Panel(5, "note", "", 0.5, 0.5, 0.3, 0.3, data={"text": ""})])
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 3)
    s.feed([palm_at(0.5, 0.5)], 2)
    assert ("edit", 5) in s.cmds
    s.engine.type_text("hi there\b\nok")
    assert s.p(5).data["text"] == "hi ther\nok" and s.engine.dirty


def test_tapping_a_picture_shows_the_next_one():
    s = make_sim()
    p = s.engine.add(Panel(6, "image", "", 0.5, 0.5, 0.3, 0.3))
    s.engine.fill(p, "image", "C:/Pics/a.jpg")
    s.feed([palm_at(0.5, 0.5)], 10)
    s.feed([palm_at(0.5, 0.5, "fist")], 3)
    s.feed([palm_at(0.5, 0.5)], 2)
    assert p.data["path"] == "C:/Pics/b.png" and p.title == "b.png"


def test_folder_entries_open_on_a_tap_and_come_out_when_pulled():
    s = make_sim()
    f = s.engine.add(Panel(7, "folder", "", 0.5, 0.5, 0.4, 0.5, z=0.0))
    s.engine.fill(f, "folder", "C:/Home")
    tiles = s.engine.tiles(f)
    labels = [label for _, label, _ in tiles]
    assert labels[0] == "⬑" and "cube.obj" in labels

    cube = next(r for r, label, _ in tiles if label == "cube.obj")
    c = (cube[0] + cube[2] / 2, cube[1] + cube[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "fist")], 2)
    s.path(c, (0.85, 0.3), 12, "fist")
    s.feed([palm_at(0.85, 0.3, "fist")], 15)
    made = [cmd[1] for cmd in s.cmds if cmd[0] == "create"]
    assert len(made) == 1
    m = s.engine.panel(made[0])
    assert m.kind == "model" and m.data["ref"] == "C:/Home/cube.obj"
    assert math.dist((m.x, m.y), (0.85, 0.3)) < 0.03 and (f.x, f.y) == (0.5, 0.5)
    s.feed([palm_at(0.85, 0.3)], 3)

    pics = next(r for r, label, _ in s.engine.tiles(f) if label == "Pics")
    c = (pics[0] + pics[2] / 2, pics[1] + pics[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "fist")], 3)
    s.feed([palm_at(*c)], 2)
    assert f.data["path"] == "C:/Pics" and [label for _, label, _ in s.engine.tiles(f)][1:] == ["a.jpg", "b.png"]


def test_music_buttons():
    s = make_sim([Panel(8, "music", "", 0.5, 0.5, 0.36, 0.3, z=0.0)])
    tiles = s.engine.tiles(s.p(8))
    nxt = next(r for r, _, act in tiles if act == ("media", "next"))
    c = (nxt[0] + nxt[2] / 2, nxt[1] + nxt[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "fist")], 3)
    s.feed([palm_at(*c)], 2)
    assert ("media", "next") in s.cmds


def test_throwing_a_made_panel_down_bins_it_but_a_window_only_goes_to_the_shelf():
    for kind, expect in (("note", "delete"), ("window", "stash")):
        s = make_sim([Panel(9, kind, "x", 0.5, 0.5, 0.2, 0.2, z=0.0)])
        s.feed([palm_at(0.5, 0.5)], 10)
        s.feed([palm_at(0.5, 0.5, "fist")], 2)
        s.path((0.5, 0.5), (0.5, 0.95), 3, "fist")
        s.feed([palm_at(0.5, 0.97)], 40)
        assert expect in s.kinds(), kind


def test_made_panels_are_saved_and_come_back():
    s = make_sim()
    n = s.engine.add(Panel(11, "note", "", 0.3, 0.4, 0.2, 0.2, data={"text": "remember"}))
    f = s.engine.add(Panel(12, "folder", "", 0.7, 0.4, 0.3, 0.4))
    s.engine.fill(f, "folder", "C:/Home")
    s.engine.add(Panel(99, "window", "w", 0.5, 0.5, 0.3, 0.3, ref=99))
    saved = s.engine.dump()
    assert {d["id"] for d in saved} == {11, 12} and all("items" not in d["data"] for d in saved)
    t = make_sim([Panel(99, "window", "w", 0.5, 0.5, 0.3, 0.3, ref=99)])
    t.engine.load(saved)
    assert t.p(11).data["text"] == "remember" and t.p(12).data["items"] and t.p(99) is not None


# ---------------------------------------------------------------- the OS panels: apps, open windows, power
OS_DATA = {"apps": [("Chrome", "C:/apps/chrome.lnk"), ("Notepad", "C:/apps/notepad.lnk")],
           "windows": [("Mail", 101, False), ("Music", 102, True)]}


def os_provide(kind, arg):
    return list(OS_DATA.get(kind, [])) if kind in OS_DATA else provide(kind, arg)


def os_sim(kind):
    s = make_sim()
    s.engine.provide = os_provide
    p = s.engine.add(Panel(20, "picker", "", 0.5, 0.5, 0.4, 0.4, z=0.0))
    s.engine.fill(p, kind)
    return s, p


def tap_tile(s, p, match):
    r = next(r for r, label, act in s.engine.tiles(p) if match(label, act))
    c = (r[0] + r[2] / 2, r[1] + r[3] / 2)
    s.feed([palm_at(*c)], 10)
    s.feed([palm_at(*c, "pinch")], 3)
    s.feed([palm_at(*c)], 2)


def test_apps_panel_starts_an_app():
    s, p = os_sim("apps")
    tap_tile(s, p, lambda label, act: label == "Notepad")
    assert ("open_file", "C:/apps/notepad.lnk") in s.cmds


def test_windows_panel_brings_a_window_forward_and_follows_what_is_open():
    s, p = os_sim("windows")
    assert [label for _, label, _ in s.engine.tiles(p)] == ["Mail", "–  Music"]
    tap_tile(s, p, lambda label, act: label == "Mail")
    assert ("focus", 101) in s.cmds
    OS_DATA["windows"] = [("Mail", 101, False)]
    try:
        v = p.ver
        s.engine.refresh()
        assert len(s.engine.tiles(p)) == 1 and p.ver > v
    finally:
        OS_DATA["windows"] = [("Mail", 101, False), ("Music", 102, True)]


def test_summon_brings_every_panel_out_and_dismiss_clears_the_screen():
    s = make_sim()
    e = s.engine
    e.panels = [Panel(1, "clock", "", 0.13, 0.15, 0.17, 0.15), Panel(2, "apps", "", 0.5, 0.45, 0.24, 0.26, stashed=True),
                Panel(3, "power", "", 0.5, 0.45, 0.16, 0.16, stashed=True), Panel(4, "note", "", 0.5, 0.45, 0.2, 0.2, stashed=True)]
    e.hidden = True
    assert e.view()["panels"] == [] and e.view()["shelf"] == []          # a clean screen
    assert e.toggle_summon() is True
    assert not any(p.stashed for p in e.panels) and len(e.view()["panels"]) == 4
    apps, power = e.panel(2), e.panel(3)
    assert (apps.x, apps.y) == (0.15, 0.55) and abs(power.x - 0.87) < 0.02 and abs(power.y - 0.79) < 0.02
    assert len({(round(p.x, 2), round(p.y, 2)) for p in e.panels}) == 4            # nothing on top of another
    assert e.toggle_summon() is False and e.view()["panels"] == []
    e.toggle_summon()
    assert (e.panel(2).x, e.panel(2).y) == (0.15, 0.55)                  # back where they were


def test_ai_panel_switches_jarvis_and_ticks_the_one_in_use():
    s, p = os_sim("ai")
    labels = [label for _, label, _ in s.engine.tiles(p)]
    assert labels[0] == "✓ Claude" and "ChatGPT" in labels           # Claude (default) is ticked
    tap_tile(s, p, lambda label, act: act == ("ai", ("chatgpt", "")))
    assert ("ai", ("chatgpt", "")) in s.cmds
    s.engine.ai_current = ("chatgpt", "")
    assert "✓ ChatGPT" in [label for _, label, _ in s.engine.tiles(p)]


def test_shutting_down_needs_a_second_tap():
    s, p = os_sim("power")
    shut = lambda label, act: act == ("power", "shutdown")
    tap_tile(s, p, shut)
    assert ("armed", "shutdown") in s.cmds and ("power", "shutdown") not in s.cmds
    assert any(label.startswith("confirm:") for _, label, _ in s.engine.tiles(p))
    tap_tile(s, p, shut)
    assert ("power", "shutdown") in s.cmds


def test_an_armed_power_button_calms_down_after_a_while():
    s, p = os_sim("power")
    tap_tile(s, p, lambda label, act: act == ("power", "restart"))
    s.feed([palm_at(0.1, 0.1)], int(5 / DT))
    s.engine.refresh()
    tap_tile(s, p, lambda label, act: act == ("power", "restart"))
    assert ("power", "restart") not in s.cmds and s.cmds.count(("armed", "restart")) == 2


def test_every_power_button_needs_two_taps():
    for action in ("lock", "exit"):            # a stray pinch once locked the PC, a stray grab quit sensor-OS
        s, p = os_sim("power")
        tile = lambda label, act, a=action: act == ("power", a)
        tap_tile(s, p, tile)
        assert ("power", action) not in s.cmds and ("armed", action) in s.cmds
        tap_tile(s, p, tile)
        assert ("power", action) in s.cmds


def _state(s, pid):
    return next(p["state"] for p in s.engine.view()["panels"] if p["id"] == pid)


def test_a_panel_dragged_off_the_screen_is_thrown_away():
    for edge in ((1.0, 0.45), (0.0, 0.45), (0.5, 1.0)):            # right, left, bottom
        s = make_sim([Panel(8, "note", "", 0.5, 0.45, 0.2, 0.2, z=0.0)])
        s.feed([palm_at(0.5, 0.45)], 10)
        s.feed([palm_at(0.5, 0.45, "fist")], 3)
        s.path((0.5, 0.45), edge, 25, "fist")                       # slowly: a drag, not a fling
        s.feed([palm_at(*edge, "fist")], 5)
        assert _state(s, 8) == "bin"                                 # red: letting go throws it away
        s.feed([palm_at(*edge)], 3)
        assert ("delete", 8) in s.cmds and s.p(8) is None, edge


def test_a_panel_let_go_still_on_the_screen_stays():
    s = make_sim([Panel(8, "note", "", 0.5, 0.45, 0.2, 0.2, z=0.0)])
    s.feed([palm_at(0.5, 0.45)], 10)
    s.feed([palm_at(0.5, 0.45, "fist")], 3)
    s.path((0.5, 0.45), (0.85, 0.45), 25, "fist")                   # near the edge, but mostly on screen
    s.feed([palm_at(0.85, 0.45, "fist")], 5)
    assert _state(s, 8) == "held"
    s.feed([palm_at(0.85, 0.45)], 3)
    assert s.p(8) is not None and ("delete", 8) not in s.cmds


def test_a_real_window_dragged_off_the_screen_goes_to_the_shelf_not_closed():
    s = make_sim([Panel(8, "window", "Mail", 0.5, 0.45, 0.2, 0.2, z=0.0, ref=101)])
    s.feed([palm_at(0.5, 0.45)], 10)
    s.feed([palm_at(0.5, 0.45, "fist")], 3)
    s.path((0.5, 0.45), (1.0, 0.45), 25, "fist")
    s.feed([palm_at(1.0, 0.45, "fist")], 5)
    s.feed([palm_at(1.0, 0.45)], 3)
    assert s.p(8) is not None and s.p(8).stashed and ("delete", 8) not in s.cmds


def test_a_panel_held_by_its_outer_side_still_goes_off_the_screen():
    # The pointer stops at x = 1.00: held by its right side the panel used to stay fully on the screen.
    for grab, edge in (((0.58, 0.45), (1.0, 0.45)), ((0.42, 0.45), (0.0, 0.45))):
        s = make_sim([Panel(8, "note", "", 0.5, 0.45, 0.2, 0.2, z=0.0)])
        s.feed([palm_at(*grab)], 10)
        s.feed([palm_at(*grab, "fist")], 3)
        s.path(grab, edge, 25, "fist")
        s.feed([palm_at(*edge, "fist")], 30)                       # pushing at the edge: it slides on out
        assert _state(s, 8) == "bin", edge
        s.feed([palm_at(*edge)], 3)
        assert ("delete", 8) in s.cmds, edge


def test_a_hand_that_leaves_the_camera_carrying_a_panel_out_throws_it_away():
    s = make_sim([Panel(8, "note", "", 0.5, 0.45, 0.2, 0.2, z=0.0)])
    s.feed([palm_at(0.5, 0.45)], 10)
    s.feed([palm_at(0.5, 0.45, "fist")], 3)
    s.path((0.5, 0.45), (1.0, 0.45), 25, "fist")
    s.feed([], 20)                                                 # the hand went out of the picture
    assert ("delete", 8) in s.cmds and s.p(8) is None


def test_what_jarvis_is_asked_to_put_away():
    from sensor_os.space import put_away_target
    assert put_away_target("3D") == "model" and put_away_target("โมเดล 3 มิติ") == "model"
    assert put_away_target("Tab 3 d ที่เห็นหน้าจอ") == "model"          # as Google heard it in the log
    assert put_away_target("นาฬิกา") == "clock" and put_away_target("notes") == "note"


def test_jarvis_is_told_what_is_on_the_screen():
    from sensor_os.props import PropSpace
    from sensor_os.space import describe_scene
    s = make_sim([Panel(8, "clock", "", 0.5, 0.45, 0.2, 0.2), Panel(9, "note", "", 0.5, 0.45, 0.2, 0.2, stashed=True)])
    props = PropSpace(s.cfg, 16 / 9)
    props.add("builtin:knot")
    text = describe_scene(s.engine, props)
    assert "clock" in text and "shelf" in text and "3D model knot" in text


def test_new_panels_never_reuse_a_saved_id():
    s = make_sim([])
    s.engine.load([{"id": 1001, "kind": "note", "x": 0.5, "y": 0.4, "w": 0.2, "h": 0.2}])
    assert s.engine.new_id() > 1001



def test_a_touch_between_two_buttons_presses_the_nearest():
    s = make_sim([Panel(8, "music", "", 0.5, 0.5, 0.36, 0.3, z=0.0)])
    p = s.p(8)
    tiles = s.engine.tiles(p)
    play = next(r for r, _, act in tiles if act == ("media", "play"))
    nxt = next(r for r, _, act in tiles if act == ("media", "next"))
    gap_x = (play[0] + play[2] + nxt[0]) / 2                    # right in the gap
    y = nxt[1] + nxt[3] / 2
    assert s.engine.hit((gap_x + 0.001, y)).where == "tile"
    s.feed([palm_at(gap_x + 0.001, y)], 10)
    s.feed([palm_at(gap_x + 0.001, y, "pinch")], 3)
    s.feed([palm_at(gap_x + 0.001, y)], 2)
    assert ("media", "next") in s.cmds
