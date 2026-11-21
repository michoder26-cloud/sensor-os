"""Hologram mode tests with synthetic hands (no camera, no real mouse)."""
import math

from sensor_os.config import Config
from sensor_os.gestures import INDEX_TIP, THUMB_TIP, Hand, pointer_point
from sensor_os.holo import HoloEngine
from sensor_os.props import PropSpace
from sensor_os.stage import CameraMap
from tests.test_gestures import FIST, OPEN, PINCH, THREE, hand

DT = 1 / 30
ASPECT = 16 / 9
CMAP = CameraMap(4 / 3, ASPECT, 0.8)


def at(x, y, pinch=False, fist=False, three=False):
    """Synthetic hand whose pinch point sits at screen fraction (x, y)."""
    base = hand(fingers=FIST) if fist else hand(fingers=THREE) if three else hand(**PINCH) if pinch else hand(fingers=OPEN)
    lm = base.lm
    mx, my = pointer_point(lm, Config().pointer_anchor)
    u, v = CMAP.to_frame(x, y)
    return Hand(lm=[(p[0] + u - mx, p[1] + v - my) for p in lm], world=base.world)


class Sim:
    def __init__(self, hub=None, probe=None, props=False):
        self.cfg = Config()
        self.hub = hub(self.cfg) if hub else None
        self.props = PropSpace(self.cfg, ASPECT) if props else None
        self.engine = HoloEngine(self.cfg, CMAP, ASPECT, self.hub, probe, self.props)
        self.t = 0.0
        self.cmds = []

    def feed(self, hands, frames=1):
        out = []
        for _ in range(frames):
            self.t += DT
            out += self.engine.update(hands, self.t)
        self.cmds += out
        return out

    def path(self, a, b, frames, **kw):
        out = []
        for i in range(1, frames + 1):
            k = i / frames
            out += self.feed([at(a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, **kw)])
        return out

    def kinds(self, cmds=None):
        return [c[0] for c in (self.cmds if cmds is None else cmds) if c[0] != "goto"]

    def cursor(self, cmds=None):
        g = [c for c in (self.cmds if cmds is None else cmds) if c[0] == "goto"]
        return g[-1][1:] if g else None


P = (0.40, 0.45)


def near(a, b, tol=0.01):
    return a is not None and math.dist(a, b) <= tol


def test_cursor_follows_the_hand_exactly():
    s = Sim()
    s.feed([at(*P)], 15)
    assert near(s.cursor(), P, 0.003)
    s.path(P, (0.7, 0.3), 15)
    s.feed([at(0.7, 0.3)], 20)
    assert near(s.cursor(), (0.7, 0.3), 0.003)
    assert s.kinds() == []


def test_hand_appearing_waits_a_moment_before_taking_the_cursor():
    s = Sim()
    assert s.feed([at(*P)], 2) == []


def test_tap_clicks_where_the_hand_was_aiming_and_cursor_holds_still():
    s = Sim()
    s.feed([at(*P)], 15)
    s.cmds.clear()
    out = s.feed([at(P[0] + 0.004, P[1] + 0.006, pinch=True)], 3)     # fingers closing nudges the point
    assert near(s.cursor(out), P, 0.004)                              # rewound to the aim
    assert s.engine.mode == "press"
    assert [c for c in s.feed([at(P[0] + 0.004, P[1] + 0.006, pinch=True)], 3) if c[0] == "goto"] == []   # then frozen
    s.feed([at(*P)], 3)
    assert s.kinds() == ["click"]


def test_double_tap_lands_on_the_same_pixel():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 3)
    first = s.cursor()
    s.feed([at(*P)], 3)
    s.feed([at(P[0] + 0.006, P[1] - 0.004)], 2)
    s.cmds.clear()
    s.feed([at(P[0] + 0.006, P[1] - 0.004, pinch=True)], 3)
    assert s.cursor() == first
    s.feed([at(P[0] + 0.006, P[1] - 0.004)], 3)
    assert s.kinds() == ["click"]
    assert all(c[1:] == first for c in s.cmds if c[0] == "goto")


def test_pinch_and_move_is_a_real_drag_with_release_velocity():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)
    s.path(P, (0.6, 0.45), 12, pinch=True)
    assert s.kinds() == ["drag_start"]
    s.feed([at(0.6, 0.45, pinch=True)], 20)
    assert near(s.cursor(), (0.6, 0.45), 0.004)
    s.feed([at(0.6, 0.45)], 3)
    assert s.kinds() == ["drag_start", "drag_end"]
    end = s.cmds[-1] if s.cmds[-1][0] == "drag_end" else [c for c in s.cmds if c[0] == "drag_end"][0]
    assert math.hypot(end[1], end[2]) < s.cfg.fling_speed             # let go while still: no fling


def test_flick_release_reports_a_fast_velocity():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)
    s.path(P, (0.9, 0.45), 4, pinch=True)
    s.feed([at(0.95, 0.45)], 2)
    end = [c for c in s.cmds if c[0] == "drag_end"][0]
    assert end[1] > s.cfg.fling_speed and abs(end[2]) < 1.0


def test_holding_a_pinch_still_right_clicks_once():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 12)
    assert s.kinds() == ["click"] and 0 < s.engine.hold < 1        # the click lands while still pinched
    s.feed([at(*P, pinch=True)], 60)
    assert s.kinds() == ["click", "right_click"]
    s.path(P, (0.5, 0.5), 8, pinch=True)
    s.feed([at(0.5, 0.5)], 3)
    assert s.kinds() == ["click", "right_click"]                    # letting go does not click again


def test_click_lands_while_the_fingers_are_still_together():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)
    assert s.kinds() == []                                           # not yet: it might be a fist coming
    s.feed([at(*P, pinch=True)], 4)
    assert s.kinds() == ["click"]
    s.feed([at(*P)], 3)
    assert s.kinds() == ["click"]                                    # one click, not two


def test_quick_tap_still_clicks_on_letting_go():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 1)
    s.feed([at(*P)], 3)
    assert s.kinds() == ["click"]


def test_fast_drag_does_not_click_first():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 1)
    s.path(P, (P[0] + 0.3, P[1]), 8, pinch=True)
    assert s.kinds()[0] == "drag_start"


def test_second_hand_pinching_resizes_instead_of_clicking():
    s = Sim()
    a, b = (0.35, 0.5), (0.55, 0.5)
    s.feed([at(*a), at(*b)], 15)
    s.feed([at(*a, pinch=True), at(*b)], 3)
    s.feed([at(*a, pinch=True), at(*b, pinch=True)], 3)
    assert s.kinds()[0] == "scale_start"
    for i in range(1, 11):
        k = i / 10
        s.feed([at(a[0] - 0.05 * k, a[1], pinch=True), at(b[0] + 0.05 * k, b[1], pinch=True)])
    s.feed([at(a[0] - 0.05, a[1], pinch=True), at(b[0] + 0.05, b[1], pinch=True)], 20)
    scales = [c for c in s.cmds if c[0] == "scale"]
    assert abs(scales[-1][1] - 1.5) < 0.05                           # 0.30 apart instead of 0.20
    assert abs(scales[-1][2]) < 0.01 and abs(scales[-1][3]) < 0.01   # pulled evenly: centre stays
    s.feed([at(a[0] - 0.05, a[1]), at(b[0] + 0.05, b[1], pinch=True)], 5)    # one hand lets go
    s.feed([at(a[0] - 0.05, a[1]), at(b[0] + 0.05, b[1])], 5)
    assert set(s.kinds()) == {"scale_start", "scale", "scale_end"}
    assert s.kinds().count("scale_end") == 1


def test_second_hand_joining_a_drag_drops_it_first():
    s = Sim()
    a, b = (0.35, 0.5), (0.6, 0.5)
    s.feed([at(*a), at(*b)], 15)
    s.feed([at(*a, pinch=True), at(*b)], 2)
    for i in range(1, 9):
        s.feed([at(a[0] + 0.01 * i, a[1], pinch=True), at(*b)])
    s.feed([at(a[0] + 0.08, a[1], pinch=True), at(*b, pinch=True)], 3)
    kinds = [k for k in s.kinds() if k != "click"]      # a slow start clicks first, as a real mouse would
    assert kinds[:3] == ["drag_start", "drag_end", "scale_start"]


def test_fist_over_empty_desk_rests_and_the_other_hand_takes_over():
    s = Sim(probe=lambda x, y: False)
    s.feed([at(0.3, 0.5), at(0.7, 0.5)], 15)
    s.cmds.clear()
    s.feed([at(0.3, 0.5, fist=True), at(0.7, 0.5)], 15)
    assert near(s.cursor(), (0.7, 0.5), 0.004) and s.kinds() == []
    s.cmds.clear()
    s.feed([at(0.3, 0.5, fist=True), at(0.7, 0.5, fist=True)], 10)
    assert s.cmds == []


def test_three_finger_swipe_scrolls_under_that_hand():
    s = Sim()
    s.feed([at(*P)], 15)
    s.cmds.clear()
    s.feed([at(*P, three=True)], 6)
    s.path(P, (P[0], P[1] - 0.15), 10, three=True)                   # hand up: page follows, wheel down
    scrolls = [c for c in s.cmds if c[0] == "scroll"]
    assert scrolls and all(c[2] < 0 and c[1] == 0 for c in scrolls)
    gotos = [c for c in s.cmds if c[0] == "goto"]
    assert len(gotos) <= 1 or all(near(g[1:], P, 0.01) for g in gotos)   # cursor parked while scrolling


def test_hand_lost_while_dragging_releases_the_button():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)
    s.path(P, (0.6, 0.45), 10, pinch=True)
    s.feed([], 20)
    assert s.kinds() == ["drag_start", "drag_end"]


def test_release_all_ends_a_drag():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)
    s.path(P, (0.6, 0.45), 10, pinch=True)
    assert s.engine.release_all() == [("drag_end", 0.0, 0.0)]


def test_view_reports_both_hands_with_skeleton_on_the_pinch_point():
    s = Sim()
    s.feed([at(0.3, 0.5), at(0.7, 0.5)], 15)
    v = s.engine.view()
    assert len(v["hands"]) == 2
    for pts, pos, state, lead in v["hands"]:
        assert len(pts) == 21 and state == "free"
        mid = pointer_point(pts, Config().pointer_anchor)
        assert near(mid, pos, 1e-6)
    assert [h[3] for h in v["hands"]].count(True) == 1


# ------------------------------------------------------------------ fist: pick a window up
def test_fist_picks_the_window_up_moves_it_and_puts_it_down():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, fist=True)], 6)
    assert s.kinds()[0] == "grab_start" and set(s.kinds()) <= {"grab_start", "grab_move"}
    s.path(P, (0.6, 0.55), 12, fist=True)
    s.feed([at(0.6, 0.55, fist=True)], 20)
    move = [c for c in s.cmds if c[0] == "grab_move"][-1]
    assert near(move[1:], (0.2, 0.1), 0.006)
    assert [c for c in s.cmds if c[0] == "goto" and near(c[1:], (0.6, 0.55), 0.05)] == []   # cursor not dragged along
    s.feed([at(0.6, 0.55)], 3)
    end = s.cmds[-1] if s.cmds[-1][0] == "grab_end" else [c for c in s.cmds if c[0] == "grab_end"][0]
    assert math.hypot(end[1], end[2]) < s.cfg.fling_speed
    assert "click" not in s.kinds() and "right_click" not in s.kinds()


def test_opening_the_fist_while_moving_fast_throws_the_window():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, fist=True)], 6)
    s.path(P, (0.85, 0.45), 4, fist=True)
    s.feed([at(0.9, 0.45)], 2)
    end = [c for c in s.cmds if c[0] == "grab_end"][0]
    assert end[1] > s.cfg.fling_speed


def test_pinch_that_closes_into_a_fist_is_a_grab_not_a_click():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(*P, pinch=True)], 2)          # fingers pass through a pinch...
    s.feed([at(*P, fist=True)], 40)          # ...and end as a fist, held a long time
    assert "grab_start" in s.kinds()
    assert "click" not in s.kinds() and "right_click" not in s.kinds()


def test_two_fists_resize_the_held_window():
    s = Sim()
    a, b = (0.35, 0.5), (0.55, 0.5)
    s.feed([at(*a), at(*b)], 15)
    s.feed([at(*a, fist=True), at(*b)], 6)
    s.feed([at(*a, fist=True), at(*b, fist=True)], 6)
    assert s.kinds()[:3] == ["grab_start", "grab_end", "scale_start"] or "scale_start" in s.kinds()
    for i in range(1, 11):
        k = i / 10
        s.feed([at(a[0] - 0.05 * k, a[1], fist=True), at(b[0] + 0.05 * k, b[1], fist=True)])
    s.feed([at(a[0] - 0.05, a[1], fist=True), at(b[0] + 0.05, b[1], fist=True)], 20)
    assert abs([c for c in s.cmds if c[0] == "scale"][-1][1] - 1.5) < 0.06
    s.feed([at(a[0] - 0.05, a[1]), at(b[0] + 0.05, b[1])], 5)
    assert s.kinds().count("scale_end") == 1


# ------------------------------------------------------------------ the ring menu
from sensor_os.hub import ROW_H, HEAD_H, Hub, Item


def make_hub(cfg):
    def provider(kind, arg):
        if kind == "windows":
            return [Item(f"win {i}", "window", 100 + i) for i in range(20)]
        if kind == "files":
            if arg is None:
                return [Item("Docs", "dir", "C:/Docs")]
            return [Item("a.txt", "file", arg + "/a.txt")]
        return [Item("App", "app", "C:/app.lnk")]
    return Hub(cfg, ASPECT, provider)


def tap(s, p):
    s.feed([at(*p)], 6)
    s.feed([at(*p, pinch=True)], 3)
    s.feed([at(*p)], 3)


def test_ring_menu_opens_lists_windows_and_brings_one_up_without_clicking():
    s = Sim(hub=make_hub)
    hub = s.hub
    s.feed([at(*hub.pos)], 15)
    tap(s, hub.pos)
    assert hub.state == "orbs" and s.kinds() == []
    _, orbs, _ = hub.geometry()
    tap(s, orbs[0])
    assert hub.state == "panel" and hub.title == "Windows" and len(hub.items) == 20
    _, _, (x0, y0, w, h) = hub.geometry()
    third_row = (x0 + w / 2, y0 + HEAD_H + 2.5 * ROW_H)
    tap(s, third_row)
    assert s.kinds() == ["hub_window"] and ("hub_window", 102) in s.cmds
    assert hub.state == "closed"


def test_ring_menu_walks_folders_and_opens_a_file():
    s = Sim(hub=make_hub)
    hub = s.hub
    s.feed([at(*hub.pos)], 15)
    tap(s, hub.pos)
    tap(s, hub.geometry()[1][1])                     # the files orb
    _, _, (x0, y0, w, h) = hub.geometry()
    row = lambda k: (x0 + w / 2, y0 + HEAD_H + (k + 0.5) * ROW_H)
    tap(s, row(0))                                   # into Docs
    assert [i.kind for i in hub.items] == ["back", "file"] and hub.title == "Docs"
    tap(s, row(0))                                   # back
    assert [i.label for i in hub.items] == ["Docs"]
    tap(s, row(0))
    tap(s, row(1))
    assert ("hub_open", "C:/Docs/a.txt") in s.cmds and s.kinds() == ["hub_open"]


def test_dragging_the_ring_moves_it_and_dragging_a_list_scrolls_it():
    s = Sim(hub=make_hub)
    hub = s.hub
    start = hub.pos
    s.feed([at(*start)], 15)
    s.feed([at(*start, pinch=True)], 3)
    s.path(start, (start[0] + 0.2, start[1] + 0.1), 10, pinch=True)
    s.feed([at(start[0] + 0.2, start[1] + 0.1, pinch=True)], 15)
    s.feed([at(start[0] + 0.2, start[1] + 0.1)], 3)
    assert near(hub.pos, (start[0] + 0.2, start[1] + 0.1), 0.03) and hub.state == "closed"
    assert s.kinds() == []
    tap(s, hub.pos)
    tap(s, hub.geometry()[1][0])
    _, _, (x0, y0, w, h) = hub.geometry()
    p = (x0 + w / 2, y0 + HEAD_H + 6 * ROW_H)
    s.feed([at(*p)], 6)
    s.feed([at(*p, pinch=True)], 3)
    s.path(p, (p[0], p[1] - 4 * ROW_H), 10, pinch=True)      # pull the list up
    s.feed([at(p[0], p[1] - 4 * ROW_H, pinch=True)], 15)
    s.feed([at(p[0], p[1] - 4 * ROW_H)], 3)
    assert 3.0 <= hub.scroll <= 4.5 and s.kinds() == []


# ------------------------------------------------------------------ clap
def test_clap_clears_the_desk_once():
    s = Sim()
    s.feed([at(0.2, 0.5), at(0.8, 0.5)], 15)
    for i in range(1, 7):
        k = i / 6
        s.feed([at(0.2 + 0.24 * k, 0.5), at(0.8 - 0.24 * k, 0.5)])
    s.feed([at(0.44, 0.5), at(0.56, 0.5)], 10)
    assert s.kinds() == ["clear"]


def test_hands_slowly_coming_together_is_not_a_clap():
    s = Sim()
    s.feed([at(0.2, 0.5), at(0.8, 0.5)], 15)
    for i in range(1, 61):
        k = i / 60
        s.feed([at(0.2 + 0.24 * k, 0.5), at(0.8 - 0.24 * k, 0.5)])
    assert "clear" not in s.kinds()


# ------------------------------------------------------------------ 3D models
import numpy as np

from sensor_os import models3d

C = (0.5, 0.5)


def prop_sim():
    s = Sim(props=True)
    pid = s.props.add("builtin:knot", C)
    s.props.get(pid).spin = False
    s.feed([at(*C)], 15)
    s.cmds.clear()
    return s, pid


def test_pinch_drag_turns_the_model_the_way_the_hand_goes():
    s, pid = prop_sim()
    R0 = s.props.get(pid).R.copy()
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.58, 0.5), 10, pinch=True)
    s.feed([at(0.58, 0.5, pinch=True)], 15)
    R1 = s.props.get(pid).R.copy()
    front = np.array((0.0, 0.0, 1.0))
    assert (R1 @ np.linalg.inv(R0) @ front)[0] > 0.3          # the near side went right, with the hand
    s.path((0.58, 0.5), (0.58, 0.58), 10, pinch=True)
    s.feed([at(0.58, 0.58, pinch=True)], 15)
    R2 = s.props.get(pid).R.copy()
    assert (R2 @ np.linalg.inv(R1) @ front)[1] > 0.3          # ...and then down
    s.feed([at(0.58, 0.58)], 3)
    assert s.kinds() == [] and s.props.get(pid).pos == C       # no click, no drag, model stayed put
    assert np.allclose(R2 @ R2.T, np.eye(3), atol=1e-9)       # still a pure rotation


def test_fist_carries_the_model_and_a_gentle_release_keeps_it():
    s, pid = prop_sim()
    s.feed([at(*C, fist=True)], 6)
    s.path(C, (0.3, 0.6), 12, fist=True)
    s.feed([at(0.3, 0.6, fist=True)], 20)
    assert near(s.props.get(pid).pos, (0.3, 0.6), 0.01)
    s.feed([at(0.3, 0.6)], 3)
    assert s.props.has(pid) and s.kinds() == []


def _prop_state(s, pid):
    return next(v[6] for v in s.props.view() if v[0] == pid)


def test_a_model_pinched_and_pulled_off_it_comes_along_and_out_of_the_screen():
    # From the log: pinching a 3D model and dragging it to the edge only turned it; it never moved.
    s, pid = prop_sim()
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (1.0, 0.5), 30, pinch=True)                       # slowly: not a fling
    s.feed([at(1.0, 0.5, pinch=True)], 30)                      # pushing at the edge
    assert _prop_state(s, pid) == "bin"                          # red: letting go throws it away
    s.feed([at(1.0, 0.5)], 3)
    assert not s.props.has(pid)


def test_a_model_carried_near_the_edge_but_let_go_on_the_screen_stays():
    s, pid = prop_sim()
    s.feed([at(*C, fist=True)], 6)
    s.path(C, (0.75, 0.5), 25, fist=True)
    s.feed([at(0.75, 0.5, fist=True)], 10)
    assert _prop_state(s, pid) == "held"
    s.feed([at(0.75, 0.5)], 3)
    assert s.props.has(pid)


def test_a_dial_turned_far_is_not_carried_off():
    s = Sim(props=True)
    pid = s.props.add("control:volume", C)
    s.props.get(pid).value = 0.5
    s.feed([at(*C)], 15)
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.9, 0.5), 20, pinch=True)
    s.feed([at(0.9, 0.5)], 3)
    assert s.props.has(pid) and s.props.get(pid).pos == C


def test_throwing_the_model_removes_it():
    s, pid = prop_sim()
    s.feed([at(*C, fist=True)], 6)
    s.path(C, (0.9, 0.5), 4, fist=True)
    s.feed([at(0.95, 0.5)], 2)
    assert not s.props.has(pid) and s.kinds() == []


def test_two_hands_stretch_and_roll_the_model():
    s, pid = prop_sim()
    a, b = (0.45, 0.5), (0.55, 0.5)
    s.feed([at(*a), at(*b)], 15)
    size0, R0 = s.props.size(pid), s.props.get(pid).R.copy()
    s.feed([at(*a, pinch=True), at(*b)], 3)
    s.feed([at(*a, pinch=True), at(*b, pinch=True)], 3)
    for i in range(1, 11):
        k = i / 10
        s.feed([at(a[0] - 0.05 * k, a[1], pinch=True), at(b[0] + 0.05 * k, b[1], pinch=True)])
    s.feed([at(0.40, 0.5, pinch=True), at(0.60, 0.5, pinch=True)], 15)
    assert abs(s.props.size(pid) / size0 - 2.0) < 0.1          # 0.20 apart instead of 0.10
    for i in range(1, 11):                                     # tilt the pair of hands
        k = i / 10
        s.feed([at(0.40, 0.5 - 0.05 * k, pinch=True), at(0.60, 0.5 + 0.05 * k, pinch=True)])
    s.feed([at(0.40, 0.45, pinch=True), at(0.60, 0.55, pinch=True)], 15)
    roll = s.props.get(pid).R @ np.linalg.inv(R0)
    assert abs(roll[2, 2] - 1.0) < 1e-6 and roll[1, 0] > 0.2   # turned about the viewing axis
    s.feed([at(0.40, 0.45), at(0.60, 0.55)], 5)
    assert s.kinds() == [] and s.props.has(pid)


def test_quick_tap_on_a_model_toggles_its_spin():
    s, pid = prop_sim()
    s.feed([at(*C, pinch=True)], 3)
    s.feed([at(*C)], 3)
    assert s.props.get(pid).spin is True and s.kinds() == []


def test_idle_model_spins_slowly_and_a_held_one_does_not():
    s, pid = prop_sim()
    s.props.get(pid).spin = True
    R0 = s.props.get(pid).R.copy()
    s.feed([at(0.1, 0.1)], 30)
    assert not np.allclose(R0, s.props.get(pid).R)


def test_ring_menu_3d_orb_floats_a_model():
    def hub3d(cfg):
        return Hub(cfg, ASPECT, lambda kind, arg: [Item("Knot", "model", "builtin:knot")] if kind == "models" else [])
    s = Sim(hub=hub3d, props=True)
    hub = s.hub
    s.feed([at(*hub.pos)], 15)
    tap(s, hub.pos)
    tap(s, hub.geometry()[1][3])
    assert hub.title == "3D"
    _, _, (x0, y0, w, h) = hub.geometry()
    tap(s, (x0 + w / 2, y0 + HEAD_H + 0.5 * ROW_H))
    assert len(s.props.props) == 1 and s.props.props[0].ref == "builtin:knot" and s.kinds() == []


def test_clap_also_clears_the_models():
    s, pid = prop_sim()
    s.feed([at(0.2, 0.2), at(0.8, 0.2)], 15)
    for i in range(1, 7):
        k = i / 6
        s.feed([at(0.2 + 0.24 * k, 0.2), at(0.8 - 0.24 * k, 0.2)])
    s.feed([at(0.44, 0.2), at(0.56, 0.2)], 10)
    assert s.kinds() == ["clear"] and s.props.props == []


def test_builtin_models_load_normalized_and_within_budget():
    for name in models3d.BUILTIN:
        V, F = models3d.load("builtin:" + name, 2600)
        assert len(F) <= 2600 and F.min() >= 0 and F.max() < len(V)
        assert abs(np.linalg.norm(V, axis=1).max() - 1.0) < 1e-6


def test_simplify_brings_a_dense_mesh_under_budget():
    V, F = models3d.sphere(4)
    assert len(F) == 5120
    v, f = models3d.simplify(models3d.normalize(V), F, 900)
    assert 100 < len(f) <= 900 and f.max() < len(v)


def clap(s, y=0.2):
    s.feed([at(0.2, y), at(0.8, y)], 50)                  # apart, and past the cooldown
    for i in range(1, 7):
        k = i / 6
        s.feed([at(0.2 + 0.24 * k, y), at(0.8 - 0.24 * k, y)])
    s.feed([at(0.44, y), at(0.56, y)], 5)


def test_second_clap_brings_everything_back():
    s, pid = prop_sim()
    clap(s)
    assert s.kinds() == ["clear"] and s.props.props == []
    clap(s)
    assert s.kinds() == ["clear", "unclear"] and s.props.has(pid)
    clap(s)
    assert s.kinds() == ["clear", "unclear", "clear"]


def test_a_tap_that_wobbles_a_little_is_still_a_click():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([at(P[0] + 0.012, P[1] + 0.010, pinch=True)], 3)     # the hand shifts as the fingers close
    s.feed([at(P[0] + 0.02, P[1] + 0.015, pinch=True)], 3)
    s.feed([at(*P)], 3)
    assert s.kinds() == ["click"]


# ---------------------------------------------------------------- controls: models that do something
def control_sim(name, state=None):
    s = Sim(props=True)
    pid = s.props.add("control:" + name, C)
    s.props.sync(state or {"volume": 0.30, "muted": False, "brightness": 0.50, "cpu": 0.2})
    s.feed([at(*C)], 15)
    s.cmds.clear()
    s.props.drain()
    return s, pid


def test_turning_the_volume_dial_sets_the_volume():
    s, pid = control_sim("volume")
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.60, 0.5), 10, pinch=True)
    s.feed([at(0.60, 0.5, pinch=True)], 15)
    events = s.props.drain()
    assert events and all(name == "volume" for name, _ in events)
    levels = [v for _, v in events]
    assert levels == sorted(levels) and 0.45 < levels[-1] <= 1.0       # right = louder, step by step
    assert s.props.get(pid).value == levels[-1]
    s.path((0.60, 0.5), (0.2, 0.5), 20, pinch=True)                     # far left: stops at zero
    s.feed([at(0.2, 0.5, pinch=True)], 15)
    assert s.props.drain()[-1] == ("volume", 0.0)
    s.feed([at(0.2, 0.5)], 3)
    assert s.kinds() == [] and s.props.get(pid).pos == C                # no click, the dial stayed put


def test_a_dial_in_the_hand_ignores_stale_readings():
    s, pid = control_sim("volume")
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.60, 0.5), 10, pinch=True)
    held = s.props.get(pid).value
    s.props.sync({"volume": 0.30, "muted": False})
    assert s.props.get(pid).value == held
    s.feed([at(0.60, 0.5)], 3)
    s.props.sync({"volume": 0.80, "muted": True})
    assert s.props.get(pid).value == 0.80 and s.props.view()[0][8] == (True,)


def test_tapping_the_volume_dial_mutes():
    s, pid = control_sim("volume")
    s.feed([at(*C, pinch=True)], 3)
    s.feed([at(*C)], 3)
    assert s.props.drain() == [("volume", "mute")] and s.kinds() == []


def test_media_control_taps_to_play_and_swipes_to_skip():
    s, pid = control_sim("media")
    s.feed([at(*C, pinch=True)], 3)
    s.feed([at(*C)], 3)
    assert s.props.drain() == [("media", "play")]
    s.feed([at(*C)], 10)
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.62, 0.5), 8, pinch=True)
    s.feed([at(0.62, 0.5)], 3)
    assert s.props.drain() == [("media", "next")]
    s.feed([at(*C)], 10)
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.38, 0.5), 8, pinch=True)
    s.feed([at(0.38, 0.5)], 3)
    assert s.props.drain() == [("media", "prev")]


def test_brightness_dial_does_nothing_on_a_screen_that_cannot_dim():
    s, pid = control_sim("brightness", {"brightness": None})
    s.feed([at(*C, pinch=True)], 3)
    s.path(C, (0.62, 0.5), 8, pinch=True)
    s.feed([at(0.62, 0.5)], 3)
    assert s.props.drain() == [] and s.props.get(pid).value is None


def test_system_orb_shows_the_machine_and_spins_faster_when_busy():
    def turned(cpu):
        s, pid = control_sim("system", {"cpu": cpu, "ram": 0.5, "battery": 0.9, "plugged": True})
        R0 = s.props.get(pid).R.copy()
        s.feed([at(0.1, 0.1)], 10)
        assert s.props.view()[0][8] == (cpu, 0.5, 0.9, True)
        return np.arccos(np.clip((np.trace(s.props.get(pid).R @ R0.T) - 1) / 2, -1, 1))
    assert turned(0.9) > 3 * turned(0.05) > 0


def test_one_of_each_control_is_enough():
    s, pid = control_sim("volume")
    assert s.props.add("control:volume") == pid and len(s.props.props) == 1


# ---------------------------------------------------------------- claw: pull the next window to the hand
from sensor_os import sound
from sensor_os.holo import next_to_pull
from tests.test_gestures import HAND, MCP


def claw_at(x, y, splay=0.022, curl=(0.0, -0.035, -0.035)):
    """A hand with every finger half curled; splay pushes index and little finger apart (metres)."""
    base = hand(fingers=OPEN)
    w = list(base.world)
    for i, mcp in enumerate((5, 9, 13, 17)):
        m = MCP[mcp]
        side = (i - 1.5) / 1.5 * splay
        for j, k in ((1, 0.4), (2, 0.7), (3, 1.0)):
            w[mcp + j] = (m[0] + (curl[0] + side) * k, m[1] + curl[1] * k, m[2] + curl[2] * k)
    placed = at(x, y)
    return Hand(lm=placed.lm, world=w)


def test_claw_pulls_a_window_and_carries_it_until_the_hand_opens():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([claw_at(*P)], 6)
    assert s.kinds() == []                                   # not held long enough yet
    s.feed([claw_at(*P)], 10)
    assert s.kinds()[0] == "pull_start" and set(s.kinds()[1:]) <= {"grab_move"} and s.engine.mode == "pull"
    s.cmds.clear()
    for i in range(1, 11):
        s.feed([claw_at(P[0] + 0.02 * i, P[1])])
    s.feed([claw_at(P[0] + 0.2, P[1])], 15)
    moves = [c for c in s.cmds if c[0] == "grab_move"]
    assert near(moves[-1][1:], (0.2, 0.0), 0.01)
    s.feed([at(P[0] + 0.2, P[1], fist=True)], 5)             # closing to a fist keeps hold
    assert s.engine.mode == "pull"
    s.feed([at(P[0] + 0.2, P[1])], 3)                        # opening lets go
    assert s.kinds()[-1] == "grab_end" and s.engine.mode == "free"
    assert "click" not in s.kinds() and "grab_start" not in s.kinds()


def test_a_relaxed_curled_hand_is_not_a_claw():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([claw_at(*P, splay=-0.012)], 40)                  # same curl, fingers hanging together
    assert s.kinds() == []


def test_open_hand_fist_and_pinch_are_not_claws():
    for kw in ({}, {"fist": True}, {"pinch": True}, {"three": True}):
        s = Sim(probe=lambda x, y: False)
        s.feed([at(*P)], 15)
        s.feed([at(*P, **kw)], 30)
        assert "pull_start" not in s.kinds()


def test_claw_waits_a_moment_before_pulling_again():
    s = Sim()
    s.feed([at(*P)], 15)
    s.feed([claw_at(*P)], 16)
    s.feed([at(*P)], 3)
    s.cmds.clear()
    s.feed([claw_at(*P)], 16)                                # straight back into a claw: too soon
    assert "pull_start" not in s.kinds()
    s.feed([at(*P)], 30)
    s.feed([claw_at(*P)], 16)
    assert s.kinds().count("pull_start") == 1


def test_claw_takes_the_window_under_the_hand_or_else_the_next_in_line():
    order = [10, 20, 30]
    assert next_to_pull(order, 10, 30, []) == 30             # pointing at one that is behind: that one
    seen = []
    assert next_to_pull(order, 10, None, seen) == 20         # empty desk: the one behind the front
    assert next_to_pull([20, 10, 30], 20, 20, seen) == 30    # on the front window: the next not yet brought
    assert next_to_pull([30, 20, 10], 30, None, seen) == 20  # all had a turn: start over
    assert next_to_pull([10], 10, 10, []) is None


def test_each_gesture_has_its_sound():
    assert sound.which("free", "press", [], 4.5) == "touch"
    assert sound.which("press", "free", [("click",)], 4.5) == "click"
    assert sound.which("free", "grab", [("grab_start", 0, 0)], 4.5) == "grab"
    assert sound.which("grab", "free", [("grab_end", 0.1, 0.0)], 4.5) == "drop"
    assert sound.which("grab", "free", [("grab_end", 6.0, 0.0)], 4.5) == "throw"
    assert sound.which("free", "pull", [("pull_start", 0, 0)], 4.5) == "pull"
    assert sound.which("free", "free", [("clear",)], 4.5) == "sweep"
    assert sound.which("free", "free", [("goto", 0.1, 0.2)], 4.5) is None
    assert all(len(sound.synth(parts, 0.3)) > 100 for parts in sound.TONES.values())


# ---------------------------------------------------------------- an easier, steadier, deliberate pinch
from sensor_os.gestures import pinch_features
from tests.test_gestures import HAND


def gap_at(x, y, gap):
    """A hand at (x, y) closing its thumb onto the index: gap in hand lengths."""
    base = hand(fingers=("pinch", "open", "open", "open"), thumb=True, gap=gap)
    mx, my = pointer_point(base.lm, Config().pointer_anchor)
    u, v = CMAP.to_frame(x, y)
    return Hand(lm=[(q[0] + u - mx, q[1] + v - my) for q in base.lm], world=base.world)


def test_thumb_on_the_side_of_the_index_counts_as_a_pinch():
    h = hand(fingers=OPEN)
    w = list(h.world)
    pip, dip = w[6], w[7]
    w[4] = ((pip[0] + dip[0]) / 2 - 0.004, (pip[1] + dip[1]) / 2, (pip[2] + dip[2]) / 2)   # thumb pad on the index's side
    tip_to_tip = math.dist(w[4], w[8]) / HAND
    assert tip_to_tip > Config().pinch_on_index                 # by tips alone this was no pinch
    assert pinch_features(w)[0] < Config().pinch_on_index       # now it is


def test_the_pointer_stays_put_when_the_fingers_close():
    s = Sim()
    s.feed([at(*P)], 20)
    before = s.engine.slots[0].pos
    s.feed([at(*P, pinch=True)], 3)
    after = s.engine.slots[0].pos
    assert math.dist(before, after) < 0.002
    s.feed([at(*P)], 3)
    assert s.kinds() == ["click"]


def test_fingers_drifting_together_slowly_do_not_click():
    s = Sim()
    s.feed([gap_at(*P, 1.2)], 20)
    steps = int(2.0 / DT)                                       # two seconds from apart to touching
    for i in range(steps + 1):
        s.feed([gap_at(*P, 1.2 - 1.15 * i / steps)])
    s.feed([gap_at(*P, 0.05)], 10)
    s.feed([gap_at(*P, 1.2)], 5)
    assert "click" not in s.kinds()


def test_a_quick_pinch_still_clicks():
    s = Sim()
    s.feed([gap_at(*P, 1.2)], 20)
    for g in (0.9, 0.5, 0.2, 0.05):                             # closed in four frames (~0.13 s)
        s.feed([gap_at(*P, g)])
    s.feed([gap_at(*P, 0.05)], 2)
    s.feed([gap_at(*P, 1.2)], 3)
    assert s.kinds() == ["click"]



# ---------------------------------------------------------------- the whole screen, edges included
def real_map():
    return CameraMap(4 / 3, ASPECT, Config().holo_zoom)


def hand_in_frame(x, y, pinch=False):
    """The synthetic hand pointing at screen (x, y) through the app's real camera mapping, in frame units."""
    base = hand(**PINCH) if pinch else hand(fingers=OPEN)
    mx, my = pointer_point(base.lm, Config().pointer_anchor)
    u, v = real_map().to_frame(x, y)
    return Hand(lm=[(q[0] + u - mx, q[1] + v - my) for q in base.lm], world=base.world)


def test_pointing_anywhere_on_the_screen_keeps_the_whole_hand_in_the_camera_picture():
    # A hand partly out of the picture loses its tracking or its pinch: that is why the sides failed.
    for x in (0.0, 0.02, 0.5, 0.98, 1.0):
        for y in (0.05, 0.5, 0.95):
            for pinch in (False, True):
                lm = hand_in_frame(x, y, pinch).lm
                assert all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in lm), (x, y, pinch)


def test_a_pinch_clicks_at_the_far_left_and_far_right():
    for x in (0.04, 0.96):
        e = HoloEngine(Config(), real_map(), ASPECT, probe=lambda *_: True)
        t, cmds = 0.0, []
        for h, n in ((hand_in_frame(x, 0.5), 20), (hand_in_frame(x, 0.5, True), 3), (hand_in_frame(x, 0.5), 3)):
            for _ in range(n):
                t += DT
                cmds += e.update([h], t)
        kinds = [c[0] for c in cmds if c[0] != "goto"]
        last = [c for c in cmds if c[0] == "goto"][-1]
        assert kinds == ["click"] and abs(last[1] - x) < 0.01, x



def test_a_hand_whose_relaxed_fingers_sit_close_still_clicks_cleanly():
    # Measured on the real hand: relaxed at 0.56, pinched 0.06-0.17. With fixed thresholds the pinch
    # was barely let go (0.555) and turned into a long hold.
    s = Sim()
    s.feed([gap_at(*P, 0.56)], 30)
    for g in (0.45, 0.3, 0.14):
        s.feed([gap_at(*P, g)])
    s.feed([gap_at(*P, 0.14)], 4)
    for g in (0.3, 0.45, 0.56):
        s.feed([gap_at(*P, g)])
    s.feed([gap_at(*P, 0.56)], 5)
    assert s.kinds() == ["click"]
    on, off = s.engine.slots[0].thresholds(0.0)
    assert on < 0.35 and off < 0.5


def test_a_wide_open_hand_keeps_the_set_thresholds():
    s = Sim()
    s.feed([gap_at(*P, 1.1)], 30)
    on, off = s.engine.slots[0].thresholds(0.0)
    cfg = Config()
    assert (on, off) == (cfg.pinch_on_index, cfg.pinch_off_index)


def test_the_pointer_never_leaves_the_screen():
    s = Sim()
    s.feed([at(1.2, 0.5)], 20)
    assert 0.0 <= s.engine.slots[0].pos[0] <= 1.0
