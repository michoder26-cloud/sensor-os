"""Gesture state machine tests with synthetic hands (no camera needed)."""
import math
import random

from sensor_os.config import Config
from sensor_os.gestures import GestureEngine, Hand, pinch_features

DT = 1 / 30
HAND = 0.09            # metres, wrist -> middle knuckle
ASPECT = 640 / 480
SY = 0.2 / HAND        # the hand spans 0.2 of the frame height
SX = SY / ASPECT

MCP = {5: (-0.025, -0.088, 0.0), 9: (0.0, -0.090, 0.0), 13: (0.020, -0.085, 0.0), 17: (0.040, -0.075, 0.0)}
SHAPES = {                       # fingertip offset from its knuckle
    "open": (0.0, -0.080, 0.0),
    "pinch": (0.0, -0.040, -0.040),
    "fold": (0.0, 0.030, -0.020),
}
THUMB_REST = (-0.070, -0.050, -0.020)
OPEN = ("open",) * 4
FIST = ("fold",) * 4
THREE = ("open", "open", "open", "fold")


def hand(fingers=OPEN, thumb=False, gap=0.0, dx=0.0, dy=0.0):
    """Synthetic hand.

    fingers  shape of index, middle, ring, pinky
    thumb    True puts the thumb tip next to the index fingertip
    gap      thumb-to-fingertip distance in hand-lengths
    dx, dy   position offset in frame heights
    """
    w = [(0.0, 0.0, 0.0)] * 21
    for i, mcp in enumerate((5, 9, 13, 17)):
        m = MCP[mcp]
        off = SHAPES[fingers[i]]
        w[mcp] = m
        w[mcp + 1] = tuple(m[k] + off[k] * 0.4 for k in range(3))
        w[mcp + 2] = tuple(m[k] + off[k] * 0.7 for k in range(3))
        w[mcp + 3] = tuple(m[k] + off[k] for k in range(3))
    tip = (w[8][0] - gap * HAND, w[8][1], w[8][2]) if thumb else THUMB_REST
    w[1], w[2], w[3], w[4] = (-0.02, -0.02, 0.0), (-0.04, -0.035, -0.01), (-0.055, -0.045, -0.015), tip
    lm = [(0.5 + p[0] * SX + dx / ASPECT, 0.6 + p[1] * SY + dy) for p in w]
    return Hand(lm=lm, world=w)


PINCH = dict(fingers=("pinch", "open", "open", "open"), thumb=True, gap=0.05)
PINCH_CURLED = dict(fingers=("pinch", "fold", "fold", "fold"), thumb=True, gap=0.05)   # "OK" with other fingers tucked


class Sim:
    def __init__(self, cfg=None, second=None):
        self.engine = GestureEngine(cfg or Config())
        self.t = 0.0
        self.events = []
        self.feed(hand(), 10, second)   # let the hand "appear" and settle
        self.events.clear()

    def feed(self, h, frames=1, second=None):
        out = []
        for _ in range(frames):
            self.t += DT
            out += self.engine.update(h, self.t, second)
        self.events += out
        return out

    def glide(self, dx=0.0, dy=0.0, frames=10, x0=0.0, y0=0.0, **kw):
        """Move the hand smoothly from (x0, y0) by (dx, dy)."""
        out = []
        for i in range(1, frames + 1):
            out += self.feed(hand(dx=x0 + dx * i / frames, dy=y0 + dy * i / frames, **kw))
        return out

    def kinds(self):
        return [e.kind for e in self.events if e.kind != "move"]

    def travel(self):
        ev = [e for e in self.events if e.kind == "move"]
        return round(sum(e.x for e in ev), 9), round(sum(e.y for e in ev), 9)


def test_synthetic_hand_features():
    p, reach = pinch_features(hand().world)
    assert p > 0.8 and min(reach) > 1.5      # well apart (the side of the index counts too)
    p, reach = pinch_features(hand(**PINCH).world)
    assert p < 0.1 and reach[0] > 1.05
    _, reach = pinch_features(hand(fingers=FIST).world)
    assert max(reach) < 0.95


# ------------------------------------------------------------------ open hand: cursor
def test_moving_hand_moves_cursor_same_direction():
    s = Sim()
    s.glide(dx=0.2, frames=15)
    x, y = s.travel()
    assert x > 0.05 and abs(y) < 0.01
    s.feed(hand(dx=0.2), 30)
    s.events.clear()
    s.glide(dy=-0.2, frames=15, x0=0.2)
    x, y = s.travel()
    assert y < -0.05 and abs(x) < 0.01


def test_relaxed_half_open_hand_still_moves_cursor():
    s = Sim()
    s.glide(dx=0.2, frames=15, fingers=("open", "fold", "fold", "open"))
    assert s.travel()[0] > 0.05 and s.kinds() == []


def test_fast_motion_travels_farther_than_slow():
    slow, fast = Sim(), Sim()
    slow.glide(dx=0.2, frames=90)
    fast.glide(dx=0.2, frames=6)
    fast.feed(hand(dx=0.2), 10)
    assert fast.travel()[0] > 2 * slow.travel()[0] > 0


def test_noisy_still_hand_does_not_drift():
    s = Sim()
    rnd = random.Random(7)
    for _ in range(300):
        s.feed(hand(dx=rnd.gauss(0, 0.0015), dy=rnd.gauss(0, 0.0015)))
    assert math.hypot(*s.travel()) < 0.01


def test_hand_reappearing_elsewhere_does_not_jump():
    s = Sim()
    s.feed(None, 15)
    assert s.engine.mode == "lost"
    s.feed(hand(dx=0.4, dy=-0.2), 10)
    assert s.travel() == (0, 0)


# ------------------------------------------------------------------ pinch: click / drag / right click
def test_tap_is_one_click_and_cursor_stays_put():
    s = Sim()
    s.feed(hand(**PINCH), 3)
    assert s.engine.mode == "press"
    s.feed(hand(), 4)
    assert s.kinds() == ["click"]
    assert s.travel() == (0, 0)


def test_tap_with_other_fingers_curled_is_a_click_not_a_grab():
    s = Sim()
    s.feed(hand(**PINCH_CURLED), 3)
    assert s.engine.mode == "press"
    s.feed(hand(), 4)
    assert s.kinds() == ["click"]


def test_quick_tap_registers_at_low_frame_rate():
    global DT
    old, DT = DT, 1 / 17
    try:
        s = Sim()
        s.feed(hand(**PINCH), 2)
        s.feed(hand(), 2)
        assert s.kinds() == ["click"]
    finally:
        DT = old


def test_double_tap_with_jitter_gives_two_clicks_on_same_spot():
    s = Sim()
    s.feed(hand(**PINCH), 3)
    s.feed(hand(), 2)
    s.feed(hand(dx=0.004, dy=0.003), 2)
    s.feed(hand(dx=0.004, dy=0.003, **PINCH), 3)
    s.feed(hand(dx=0.004, dy=0.003), 2)
    assert s.kinds() == ["click", "click"]
    assert s.travel() == (0, 0)


def test_pinch_and_move_drags():
    s = Sim()
    s.feed(hand(**PINCH), 2)
    s.glide(dx=0.15, frames=10, **PINCH)
    assert s.kinds() == ["drag_start"]
    assert s.travel()[0] > 0.05
    s.feed(hand(dx=0.15), 3)
    assert s.kinds() == ["drag_start", "drag_end"]


def test_holding_pinch_still_is_one_right_click():
    s = Sim()
    s.feed(hand(**PINCH), 12)                       # 0.4 s: not yet
    assert s.kinds() == [] and 0 < s.engine.hold < 1
    s.feed(hand(**PINCH), 60)                       # well past right_hold
    assert s.kinds() == ["right_click"] and s.engine.mode == "held"
    s.glide(dx=0.1, frames=10, **PINCH)             # moving afterwards does nothing
    s.feed(hand(dx=0.1), 4)                         # release: no extra click
    assert s.kinds() == ["right_click"]
    assert s.travel() == (0, 0)


def test_hysteresis_no_chatter_near_threshold():
    s = Sim()
    near = dict(fingers=("pinch", "open", "open", "open"), thumb=True)
    for gap in (0.36, 0.44, 0.33, 0.42) * 5:        # between on (0.30) and off (0.48)
        s.feed(hand(gap=gap, **near))
    assert s.kinds() == []
    s.feed(hand(**PINCH), 2)
    for gap in (0.36, 0.44, 0.33, 0.42):
        s.feed(hand(gap=gap, **near))
    assert s.kinds() == []                          # still held
    s.feed(hand(), 3)
    assert s.kinds() == ["click"]


def test_hand_lost_during_drag_releases_button():
    s = Sim()
    s.feed(hand(**PINCH), 2)
    s.glide(dx=0.15, frames=10, **PINCH)
    s.feed(None, 3)
    assert s.kinds() == ["drag_start"]
    s.feed(None, 10)
    assert s.kinds() == ["drag_start", "drag_end"]
    assert s.engine.mode == "lost"


def test_progress_rises_as_fingers_close():
    s = Sim()
    near = dict(fingers=("pinch", "open", "open", "open"), thumb=True)
    s.feed(hand(), 5)
    far = s.engine.progress
    s.feed(hand(gap=0.6, **near), 5)
    mid = s.engine.progress
    s.feed(hand(gap=0.32, **near), 5)
    assert far < mid < s.engine.progress <= 1.0


# ------------------------------------------------------------------ fling a window
def test_fast_release_reports_fling_velocity():
    s = Sim()
    s.feed(hand(**PINCH), 2)
    s.glide(dx=0.1, frames=15, **PINCH)
    s.glide(dx=0.5, frames=5, x0=0.1, **PINCH)
    s.feed(hand(dx=0.6), 2)
    end = [e for e in s.events if e.kind == "drag_end"][0]
    assert end.x > Config().fling_speed and abs(end.y) < 0.5


def test_slow_release_is_not_a_fling():
    s = Sim()
    s.feed(hand(**PINCH), 2)
    s.glide(dx=0.3, frames=45, **PINCH)
    s.feed(hand(dx=0.3, **PINCH), 8)
    s.feed(hand(dx=0.3), 2)
    end = [e for e in s.events if e.kind == "drag_end"][0]
    assert math.hypot(end.x, end.y) < Config().fling_speed


# ------------------------------------------------------------------ fist: rest
def test_fist_does_nothing_even_when_moving():
    s = Sim()
    s.feed(hand(fingers=FIST), 10)
    s.glide(dx=0.3, dy=-0.2, frames=12, fingers=FIST)
    assert s.engine.mode == "grab" and s.events == []
    s.feed(hand(dx=0.3, dy=-0.2), 5)
    assert s.kinds() == [] and math.hypot(*s.travel()) < 0.02


# ------------------------------------------------------------------ three fingers: swipe to scroll
def test_three_fingers_held_still_do_nothing():
    s = Sim()
    s.feed(hand(fingers=THREE), 30)
    assert s.engine.mode == "three" and s.events == []


def test_three_finger_swipe_scrolls_the_page_without_moving_the_cursor():
    s = Sim()
    s.feed(hand(fingers=THREE), 6)
    s.glide(dy=-0.15, frames=12, fingers=THREE)      # hand up: page follows, so wheel goes down
    scrolls = [e for e in s.events if e.kind == "scroll"]
    assert scrolls and all(e.y < 0 and e.x == 0 for e in scrolls)
    assert s.engine.mode == "scroll"
    assert s.kinds() == ["scroll"] * len(scrolls)
    assert s.travel() == (0, 0)


def test_three_finger_sideways_swipe_uses_horizontal_wheel():
    s = Sim()
    s.feed(hand(fingers=THREE), 6)
    s.glide(dx=0.15, frames=12, fingers=THREE)
    scrolls = [e for e in s.events if e.kind == "scroll"]
    assert scrolls and all(e.y == 0 and e.x != 0 for e in scrolls)


def test_opening_the_hand_after_a_swipe_does_not_fling_the_cursor_or_click():
    s = Sim()
    s.feed(hand(fingers=THREE), 6)
    s.glide(dy=-0.15, frames=12, fingers=THREE)
    s.events.clear()
    s.feed(hand(dy=-0.15), 5)
    assert s.kinds() == [] and math.hypot(*s.travel()) < 0.02
    assert s.engine.mode == "move"


# ------------------------------------------------------------------ two fists: zoom
def two_fists():
    other = hand(dx=0.2)
    s = Sim(second=other)
    s.feed(hand(dx=-0.2), 30, other)                # both hands in place and settled
    s.events.clear()
    s.feed(hand(dx=-0.2, fingers=FIST), 6, hand(dx=0.2, fingers=FIST))
    return s


def test_two_fists_zoom_and_nothing_else():
    s = two_fists()
    assert s.engine.mode == "zoom"
    for i in range(1, 11):                          # pull apart
        s.feed(hand(dx=-0.2 - 0.02 * i, fingers=FIST), 1, hand(dx=0.2 + 0.02 * i, fingers=FIST))
    zin = [e.y for e in s.events if e.kind == "zoom"]
    assert zin and all(v > 0 for v in zin)
    for i in range(1, 11):                          # push together
        s.feed(hand(dx=-0.4 + 0.02 * i, fingers=FIST), 1, hand(dx=0.4 - 0.02 * i, fingers=FIST))
    zout = [e.y for e in s.events if e.kind == "zoom"][len(zin):]
    assert zout and all(v < 0 for v in zout)
    s.feed(hand(dx=-0.2), 5, hand(dx=0.2))
    assert set(s.kinds()) == {"zoom"}
    assert math.hypot(*s.travel()) < 0.02


def test_no_scroll_when_second_fist_opens_first():
    s = two_fists()
    s.events.clear()
    other = hand(dx=0.2)
    s.feed(hand(dx=-0.2, fingers=FIST), 3, other)
    s.glide(dy=-0.2, frames=12, x0=-0.2, fingers=FIST)     # main fist keeps moving
    assert s.events == []


def test_open_second_hand_does_not_disturb_single_hand_use():
    other = hand(dx=0.2)
    s = Sim(second=other)
    s.feed(hand(dx=-0.2), 30, other)
    s.events.clear()
    s.feed(hand(dx=-0.2, **PINCH), 3, other)
    s.feed(hand(dx=-0.2), 3, other)
    assert s.kinds() == ["click"]
