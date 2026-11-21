"""Stage mode tests with synthetic hands (no camera, no real windows)."""
import math

from sensor_os.config import Config
from sensor_os.gestures import INDEX_TIP, THUMB_TIP, Hand, pointer_point
from sensor_os.stage import (TRAY_TOP, CameraMap, Card, DesktopMap, StageEngine, SummonDetector,
                             tray_rects)
from tests.test_gestures import FIST, OPEN, PINCH, hand

DT = 1 / 30
CMAP = CameraMap(4 / 3, 16 / 9, 0.8)
DMAP = DesktopMap(1920, 1080, 0.8)
A_SCREEN = (200, 150, 1000, 750)
B_SCREEN = (800, 400, 1700, 950)


def at(x, y, pinch=False, fist=False, wrist_dy=0.0):
    """Synthetic hand whose pinch point sits at stage (x, y)."""
    if fist:
        base = hand(fingers=FIST)
    elif pinch:
        base = hand(**PINCH)
    else:
        base = hand(fingers=OPEN)
    lm = base.lm
    mx, my = pointer_point(lm, Config().pointer_anchor)
    u, v = CMAP.to_frame(x, y)
    pts = [(p[0] + u - mx, p[1] + v - my) for p in lm]
    if wrist_dy:
        pts[0] = (pts[0][0], pts[0][1] + wrist_dy)
    return Hand(lm=pts, world=base.world)


def cards():
    # B is in front of A (lists are front-most first); they overlap.
    return [Card(2, "B", DMAP.to_stage(B_SCREEN)), Card(1, "A", DMAP.to_stage(A_SCREEN))]


class Sim:
    def __init__(self, card_list=None):
        self.cfg = Config()
        self.engine = StageEngine(self.cfg, CMAP, DMAP)
        self.engine.reset(card_list if card_list is not None else cards())
        self.t = 0.0
        self.cmds = []

    def feed(self, hands, frames=1):
        out = []
        for _ in range(frames):
            self.t += DT
            out += self.engine.update(hands, self.t)
        self.cmds += out
        return out

    def path(self, start, end, frames, **kw):
        """One hand moving in a straight line."""
        for i in range(1, frames + 1):
            k = i / frames
            self.feed([at(start[0] + (end[0] - start[0]) * k, start[1] + (end[1] - start[1]) * k, **kw)])

    def rect(self, cid):
        return self.engine.card(cid).rect


A_ONLY = (0.30, 0.30)     # a point on A not covered by B
OVERLAP = (0.50, 0.50)    # inside both; B is in front
EMPTY = (0.85, 0.15)


def test_desktop_map_round_trip():
    for r in (A_SCREEN, B_SCREEN, (0, 0, 1920, 1080)):
        assert DMAP.to_screen(DMAP.to_stage(r)) == r
    assert DMAP.to_stage((0, 0, 1920, 1080)) == DMAP.full


def test_camera_map_round_trip_and_crop():
    for p in ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.2, 0.7)):
        q = CMAP.to_stage(*CMAP.to_frame(*p))
        assert math.isclose(q[0], p[0]) and math.isclose(q[1], p[1])
    u0, v0, u1, v1 = CMAP.crop
    assert math.isclose((u1 - u0) / (v1 - v0) * (4 / 3), 16 / 9)   # crop has the screen's shape


def test_tap_on_card_focuses_it_and_leaves_once():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.feed([at(*A_ONLY)], 4)
    assert s.cmds == [("focus", 1), ("exit",)]
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.feed([at(*A_ONLY)], 4)
    assert s.cmds == [("focus", 1), ("exit",)]    # nothing after leaving


def test_tap_on_empty_space_does_nothing():
    s = Sim()
    s.feed([at(*EMPTY)], 10)
    s.feed([at(*EMPTY, pinch=True)], 3)
    s.feed([at(*EMPTY)], 4)
    assert s.cmds == []


def test_overlapping_cards_pick_the_front_one():
    s = Sim()
    s.feed([at(*OVERLAP)], 10)
    s.feed([at(*OVERLAP, pinch=True)], 3)
    s.feed([at(*OVERLAP)], 4)
    assert s.cmds[0] == ("focus", 2)


def test_hover_highlights_the_card_under_the_hand():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    states = {c[0]: c[3] for c in s.engine.view()["cards"]}
    assert states == {1: "hover", 2: ""}


def test_drag_moves_the_real_window_by_the_same_amount():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    end = (A_ONLY[0] + 0.10, A_ONLY[1] + 0.05)
    s.path(A_ONLY, end, 12, pinch=True)
    s.feed([at(*end, pinch=True)], 20)             # settle, then let go slowly
    s.feed([at(*end)], 4)
    kind, cid, rect = s.cmds[0]
    assert (kind, cid) == ("move", 1)
    dx, dy = 0.10 / 0.8 * 1920, 0.05 / 0.8 * 1080
    expect = (A_SCREEN[0] + dx, A_SCREEN[1] + dy, A_SCREEN[2] + dx, A_SCREEN[3] + dy)
    assert all(abs(a - b) <= 4 for a, b in zip(rect, expect)), (rect, expect)
    assert rect[2] - rect[0] == A_SCREEN[2] - A_SCREEN[0]     # size unchanged


def test_card_follows_the_hand_while_dragging_and_comes_to_the_front():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    before = s.rect(1)
    s.path(A_ONLY, (A_ONLY[0] + 0.1, A_ONLY[1]), 10, pinch=True)
    s.feed([at(A_ONLY[0] + 0.1, A_ONLY[1], pinch=True)], 15)
    assert abs(s.rect(1)[0] - before[0] - 0.1) < 0.01
    assert s.engine.cards[0].id == 1
    assert s.engine.view()["cards"][0][3] == "held"


def test_fast_flick_minimizes_to_the_tray():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.path(A_ONLY, (A_ONLY[0] - 0.35, A_ONLY[1]), 4, pinch=True)     # ~2.6 stage widths/s
    s.feed([at(A_ONLY[0] - 0.40, A_ONLY[1])], 3)
    assert s.cmds == [("minimize", 1)]
    view = s.engine.view()
    assert [c[0] for c in view["tray"]] == [1]
    assert 1 not in [c[0] for c in view["cards"]]


def test_slow_release_near_the_edge_is_just_a_move():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.path(A_ONLY, (0.05, A_ONLY[1]), 40, pinch=True)
    s.feed([at(0.05, A_ONLY[1], pinch=True)], 15)
    s.feed([at(0.05, A_ONLY[1])], 4)
    assert [c[0] for c in s.cmds] == ["move"]


def test_hand_lost_while_dragging_puts_the_card_back_with_no_command():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    orig = s.rect(1)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.path(A_ONLY, (A_ONLY[0] + 0.1, A_ONLY[1]), 10, pinch=True)
    s.feed([], 20)
    assert s.cmds == []
    assert s.rect(1) == orig
    assert s.engine.sessions == {}


def two(p, q, pinch_p=True, pinch_q=True):
    return [at(*p, pinch=pinch_p), at(*q, pinch=pinch_q, wrist_dy=0.0)]


def test_two_hands_pull_apart_resizes_around_the_middle():
    s = Sim()
    p0, q0 = (0.22, 0.40), (0.42, 0.40)       # both on A, left of B
    s.feed([at(*p0), at(*q0)], 10)
    orig = s.rect(1)
    s.feed(two(p0, q0), 3)
    for i in range(1, 13):
        k = i / 12
        s.feed(two((p0[0] - 0.05 * k, p0[1]), (q0[0] + 0.05 * k, q0[1])))
    s.feed(two((p0[0] - 0.05, p0[1]), (q0[0] + 0.05, q0[1])), 15)
    s.feed([at(p0[0] - 0.05, p0[1]), at(q0[0] + 0.05, q0[1])], 4)
    kind, cid, rect = s.cmds[0]
    assert (kind, cid) == ("resize", 1) and len(s.cmds) == 1
    w0 = A_SCREEN[2] - A_SCREEN[0]
    assert (rect[2] - rect[0]) / w0 > 1.3                       # 0.30 apart instead of 0.20: x1.5
    ratio0 = (A_SCREEN[3] - A_SCREEN[1]) / w0
    assert abs((rect[3] - rect[1]) / (rect[2] - rect[0]) - ratio0) < 0.02   # same shape
    cx0 = (orig[0] + orig[2] / 2)
    cx = DMAP.to_stage(rect)[0] + DMAP.to_stage(rect)[2] / 2
    assert abs(cx - cx0) < 0.02                                 # hands moved symmetrically


def test_letting_go_with_one_hand_keeps_dragging_without_a_jump():
    s = Sim()
    p0, q0 = (0.22, 0.40), (0.42, 0.40)
    s.feed([at(*p0), at(*q0)], 10)
    s.feed(two(p0, q0), 3)
    s.feed(two((0.20, 0.40), (0.44, 0.40)), 15)
    s.feed([at(0.20, 0.40, pinch=True), at(0.44, 0.40)], 4)    # right hand lets go
    before = s.rect(1)
    s.feed([at(0.20, 0.40, pinch=True), at(0.44, 0.40)], 5)
    assert all(abs(a - b) < 1e-3 for a, b in zip(before, s.rect(1)))
    s.path((0.20, 0.40), (0.25, 0.40), 6, pinch=True)
    s.feed([at(0.25, 0.40, pinch=True)], 15)
    assert abs(s.rect(1)[0] - before[0] - 0.05) < 0.01
    assert s.cmds == []


def test_pulling_nearly_full_size_maximizes():
    s = Sim([Card(1, "A", DMAP.to_stage((660, 340, 1260, 740)))])
    p0, q0 = (0.42, 0.45), (0.58, 0.45)
    s.feed([at(*p0), at(*q0)], 10)
    s.feed(two(p0, q0), 3)
    for i in range(1, 16):
        k = i / 15
        s.feed(two((p0[0] - 0.2 * k, p0[1]), (q0[0] + 0.2 * k, q0[1])))
    s.feed(two((p0[0] - 0.2, p0[1]), (q0[0] + 0.2, q0[1])), 15)
    s.feed([at(p0[0] - 0.2, p0[1]), at(q0[0] + 0.2, q0[1])], 4)
    assert s.cmds == [("maximize", 1)]
    assert s.rect(1) == DMAP.full


def test_two_fists_leave_the_stage():
    s = Sim()
    s.feed([at(0.3, 0.5), at(0.7, 0.5)], 10)
    s.feed([at(0.3, 0.5, fist=True), at(0.7, 0.5, fist=True)], 4)
    assert s.cmds == [] and 0 < s.engine.view()["exit"] < 1
    s.feed([at(0.3, 0.5, fist=True), at(0.7, 0.5, fist=True)], 15)
    assert s.cmds == [("exit",)]


def test_one_fist_does_not_leave():
    s = Sim()
    s.feed([at(0.3, 0.5, fist=True), at(0.7, 0.5)], 40)
    assert s.cmds == []


def test_hands_crossing_keep_their_identity():
    s = Sim()
    start_a, start_b = A_ONLY, (0.75, 0.30)
    s.feed([at(*start_a), at(*start_b, wrist_dy=0.0)], 10)
    s.feed([at(*start_a, pinch=True), at(*start_b)], 3)
    for i in range(1, 31):                      # the two hands pass each other
        k = i / 30
        a = at(start_a[0] + 0.30 * k, start_a[1] + 0.02 * k, pinch=True)
        b = at(start_b[0] - 0.50 * k, start_b[1] + 0.12 * k)
        s.feed([b, a] if i % 2 else [a, b])      # detector order flips too
    s.feed([at(start_a[0] + 0.30, start_a[1] + 0.02, pinch=True), at(0.25, 0.42)], 15)
    s.feed([at(start_a[0] + 0.30, start_a[1] + 0.02), at(0.25, 0.42)], 4)
    assert [c[:2] for c in s.cmds] == [("move", 1)]
    assert abs(s.rect(1)[0] - (DMAP.to_stage(A_SCREEN)[0] + 0.30)) < 0.015


def test_tray_tile_tap_restores_and_focuses():
    s = Sim([Card(1, "A", DMAP.to_stage(A_SCREEN)), Card(3, "C", DMAP.to_stage(B_SCREEN), minimized=True)])
    tile = tray_rects(1)[0]
    p = (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
    s.feed([at(*p)], 10)
    s.feed([at(*p, pinch=True)], 3)
    s.feed([at(*p)], 4)
    assert s.cmds == [("restore", 3), ("focus", 3), ("exit",)]


def test_tray_tile_dragged_up_restores_and_stays_on_stage():
    s = Sim([Card(3, "C", DMAP.to_stage(B_SCREEN), minimized=True)])
    tile = tray_rects(1)[0]
    p = (tile[0] + tile[2] / 2, tile[1] + tile[3] / 2)
    s.feed([at(*p)], 10)
    s.feed([at(*p, pinch=True)], 3)
    s.path(p, (p[0], TRAY_TOP - 0.2), 20, pinch=True)
    s.feed([at(p[0], TRAY_TOP - 0.2, pinch=True)], 15)
    s.feed([at(p[0], TRAY_TOP - 0.2)], 4)
    assert s.cmds == [("restore", 3)]
    assert [c[0] for c in s.engine.view()["cards"]] == [3]


def test_sync_keeps_a_held_card_and_a_just_moved_card():
    s = Sim()
    s.feed([at(*A_ONLY)], 10)
    s.feed([at(*A_ONLY, pinch=True)], 3)
    s.path(A_ONLY, (A_ONLY[0] + 0.1, A_ONLY[1]), 10, pinch=True)
    held = s.rect(1)
    s.engine.sync(cards(), s.t)                     # Windows still reports the old place
    assert s.rect(1) == held
    s.feed([at(A_ONLY[0] + 0.1, A_ONLY[1], pinch=True)], 15)
    s.feed([at(A_ONLY[0] + 0.1, A_ONLY[1])], 4)
    moved = s.rect(1)
    s.engine.sync(cards(), s.t)
    assert s.rect(1) == moved                       # not snapped back before the window moves
    s.engine.sync(cards(), s.t + 2.0)
    assert s.rect(1) == DMAP.to_stage(A_SCREEN)     # later, Windows is the truth again


def test_summon_needs_both_hands_open_for_a_moment_and_fires_once():
    cfg = Config()
    d = SummonDetector(cfg)
    t = 0.0
    fired = 0
    open2 = [hand(fingers=OPEN), hand(fingers=OPEN)]
    for _ in range(60):
        t += DT
        fired += d.update(open2, t)
    assert fired == 1
    for _ in range(30):                              # one hand open, one pinching: nothing
        t += DT
        fired += d.update([hand(fingers=OPEN), hand(**PINCH)], t)
    assert fired == 1
    for _ in range(30):                              # re-armed after the hands changed
        t += DT
        fired += d.update(open2, t)
    assert fired == 2


def test_single_open_hand_does_not_summon():
    d = SummonDetector(Config())
    assert not any(d.update([hand(fingers=OPEN)], i * DT) for i in range(1, 90))
