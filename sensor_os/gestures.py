"""Turns hand landmarks into input events.

Knows nothing about cameras, Windows or the HUD: feed it a Hand (2D frame
landmarks + 3D world landmarks) and a timestamp, get back a list of Events.

Three clearly different hand shapes, nothing else:
    open hand            -> cursor moves (relative, like a trackpad in the air)
    pinch thumb + index  -> tap = click, move = drag, hold still = right click
    three fingers up     -> swipe to scroll the page (index + middle + ring, little finger folded)
    fist                 -> rest: nothing moves
Plus:
    drag, release fast   -> drag_end carries the release velocity (fling a window)
    two fists            -> pull apart / push together = zoom
"""
import math
from dataclasses import dataclass

from .config import Config
from .filters import OneEuroFilter

WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_PIP = 6
INDEX_DIP = 7
INDEX_TIP = 8
MIDDLE_MCP = 9
PINKY_MCP = 17
FINGERTIPS = (8, 12, 16, 20)


@dataclass
class Hand:
    lm: list      # 21 (x, y) in mirrored frame coordinates, 0..1
    world: list   # 21 (x, y, z) in meters, hand-centered


@dataclass(frozen=True)
class Event:
    """kind: move | click | right_click | drag_start | drag_end | scroll | zoom

    move      x, y = cursor delta in screen heights
    drag_end  x, y = cursor velocity at release, screen heights/second
    scroll    x = horizontal wheel units, y = vertical wheel units (positive = up)
    zoom      y = wheel units (positive = zoom in)
    """
    kind: str
    x: float = 0.0
    y: float = 0.0


def _dist3(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def pointer_point(lm, anchor: str = "knuckles") -> tuple:
    """Where a hand points, in camera-frame fractions.

    "knuckles": between the index and middle knuckles. They do not move when the fingers close,
    so the pointer stays on its target through a pinch (pinching used to drag the aim off it).
    "pinch": between the thumb and index tips, where the fingers meet."""
    if anchor == "knuckles":
        return (lm[5][0] + lm[9][0]) / 2, (lm[5][1] + lm[9][1]) / 2
    return (lm[THUMB_TIP][0] + lm[INDEX_TIP][0]) / 2, (lm[THUMB_TIP][1] + lm[INDEX_TIP][1]) / 2


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _segment_dist3(p, a, b) -> float:
    """Distance from point p to the segment a-b, in 3D."""
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    n = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
    k = 0.0 if n == 0 else max(0.0, min(1.0, (ap[0] * ab[0] + ap[1] * ab[1] + ap[2] * ab[2]) / n))
    return _dist3(p, [a[i] + ab[i] * k for i in range(3)])


def pinch_features(world):
    """Returns (thumb-index distance, [fingertip reach x4]).

    Relative to hand size, measured in 3D so hand orientation does not matter. The thumb tip may meet
    the index tip or the side of the index's last two segments (a "key" pinch): people pinch both
    ways, and the side touch is the one that used to be missed. A fist also puts the thumb there,
    which is why every caller checks for a fist first.
    """
    size = _dist3(world[WRIST], world[MIDDLE_MCP]) or 1e-6
    thumb = world[THUMB_TIP]
    pinch = min(_dist3(thumb, world[INDEX_TIP]),
                _segment_dist3(thumb, world[INDEX_DIP], world[INDEX_TIP]),
                _segment_dist3(thumb, world[INDEX_PIP], world[INDEX_DIP])) / size
    reach = [_dist3(world[tip], world[WRIST]) / size for tip in FINGERTIPS]
    return pinch, reach


class GestureEngine:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config()
        self.aspect = self.cfg.aspect
        self._fx = OneEuroFilter(self.cfg.filter_min_cutoff, self.cfg.filter_beta)
        self._fy = OneEuroFilter(self.cfg.filter_min_cutoff, self.cfg.filter_beta)
        # Public, read by the HUD
        self.mode = "lost"       # lost | wake | move | press | drag | held | three | scroll | grab | zoom
        self.progress = 0.0      # 0 = fingers apart, 1 = pinched
        self.hold = 0.0          # 0..1 progress of the hold-for-right-click
        self.scroll_axis = "y"
        self._seen = False
        self._appear_t = 0.0
        self._lost_since = None
        self._prev = None        # (x, y, t) filtered palm centre
        self._scale = None
        self._pi = None
        self._last_t = None
        self._fist = False
        self._fist_since = None
        self._three = False      # three-finger scroll shape
        self._three_since = None
        self._second = False     # second hand is a fist
        self._t0 = 0.0
        self._acc = [0.0, 0.0]
        self._sticky_until = 0.0
        self._sticky_acc = [0.0, 0.0]
        self._hold_until = 0.0
        self._scroll_rem = 0.0
        self._drag_hist = []     # (t, mx, my) of recent drag movement
        self._zoom_prev = 0.0
        self._zoom_rem = 0.0

    # ------------------------------------------------------------------ public
    def update(self, hand: Hand | None, t: float, second: Hand | None = None) -> list[Event]:
        events: list[Event] = []
        cfg = self.cfg

        if hand is None:
            if self._seen:
                if self._lost_since is None:
                    self._lost_since = t
                if t - self._lost_since >= cfg.lost_grace:
                    self._hand_lost(events)
            return events
        if not self._seen:
            self._seen = True
            self._appear_t = t
            self._prev = None
        self._lost_since = None

        dx, dy, speed = self._motion(hand.lm, t)        # hand-lengths, hand-lengths/s
        if speed < cfg.move_deadzone or speed > cfg.max_speed:
            dx = dy = 0.0
        gain = self._gain(speed) * cfg.cursor_speed
        mx, my = dx * gain, dy * gain                   # screen heights

        pinch, reach = pinch_features(hand.world)
        # Time-based smoothing, so a quick tap still registers at low camera frame rates.
        dt = t - self._last_t if self._last_t is not None else 0.0
        self._last_t = t
        a = 1.0 - math.exp(-dt / cfg.pinch_tau) if dt > 0 else 1.0
        self._pi = pinch if self._pi is None else self._pi + a * (pinch - self._pi)
        r = self._pi / cfg.pinch_on_index
        self.progress = _clamp01((cfg.progress_open - r) / (cfg.progress_open - 1.0))
        self.hold = 0.0

        if t - self._appear_t < cfg.engage_delay:
            self.mode = "wake"
            return events

        # --- pinch in progress: click, drag or right click
        if self.mode in ("press", "drag", "held"):
            self._pinch_held(events, t, mx, my)
            return events

        self._update_fist(reach, t)
        second_fist = self._second_fist(second)

        if self.mode == "zoom":
            self._zoom_held(t, events, hand, second, second_fist)
            return events

        # --- fist: rest (two fists: zoom)
        if self._fist:
            self.mode = "grab"
            if second_fist:
                self.mode = "zoom"
                self._zoom_prev = self._hands_apart(hand, second)
                self._zoom_rem = 0.0
            return events

        # --- three fingers up: swipe to scroll
        self._update_three(reach, t)
        if self._three:
            if self.mode not in ("three", "scroll"):
                self.mode = "three"
                self._acc = [0.0, 0.0]
            self._scroll_held(events, dx, dy)
            return events
        if self.mode in ("grab", "three", "scroll"):
            self.mode = "move"
            self._hold_until = t + cfg.resume_delay   # changing hand shape must not nudge the cursor

        # --- new pinch? A folded finger cannot pinch.
        if self._pi < cfg.pinch_on_index and reach[0] > cfg.pinch_reach_min:
            self.mode = "press"
            self._t0 = t
            self._acc = [0.0, 0.0]
            return events

        # --- free cursor movement
        self.mode = "move"
        if t < self._hold_until:
            return events
        if t < self._sticky_until:
            self._sticky_acc[0] += mx
            self._sticky_acc[1] += my
            if math.hypot(*self._sticky_acc) >= cfg.sticky_move:
                self._sticky_until = 0.0
            return events
        if mx or my:
            events.append(Event("move", mx, my))
        return events

    def release_all(self) -> list[Event]:
        """Called on shutdown or emergency stop."""
        events: list[Event] = []
        self._hand_lost(events)
        return events

    # ----------------------------------------------------------------- helpers
    def _palm(self, lm):
        # The knuckles, not the wrist: they swing the most when only the wrist bends,
        # so the forearm can stay resting on the desk. They also stay put during a pinch.
        return ((lm[INDEX_MCP][0] + lm[MIDDLE_MCP][0] + lm[PINKY_MCP][0]) / 3 * self.aspect,
                (lm[INDEX_MCP][1] + lm[MIDDLE_MCP][1] + lm[PINKY_MCP][1]) / 3)

    def _motion(self, lm, t):
        """Palm-centre movement since the last frame, in hand-lengths."""
        asp = self.aspect
        cx, cy = self._palm(lm)

        def d(a, b):
            return math.hypot((lm[a][0] - lm[b][0]) * asp, lm[a][1] - lm[b][1])

        # Palm length or palm width, whichever is less foreshortened by tilt.
        raw_scale = max(d(WRIST, MIDDLE_MCP), 1.35 * d(INDEX_MCP, PINKY_MCP), 1e-4)
        self._scale = raw_scale if self._scale is None else self._scale + 0.15 * (raw_scale - self._scale)

        fx, fy = self._fx(cx, t), self._fy(cy, t)
        prev, self._prev = self._prev, (fx, fy, t)
        if prev is None or t <= prev[2]:
            return 0.0, 0.0, 0.0
        dx = (fx - prev[0]) / self._scale
        dy = (fy - prev[1]) / self._scale
        return dx, dy, math.hypot(dx, dy) / (t - prev[2])

    def _gain(self, speed: float) -> float:
        c = self.cfg
        u = _clamp01((speed - c.speed_lo) / (c.speed_hi - c.speed_lo))
        u = u * u * (3 - 2 * u)
        return c.gain_slow + (c.gain_fast - c.gain_slow) * u

    def _update_fist(self, reach, t):
        cfg = self.cfg
        if self._fist:
            if max(reach) > cfg.fist_exit:
                self._fist = False
                self._fist_since = None
        elif max(reach) < cfg.fist_enter:
            if self._fist_since is None:
                self._fist_since = t
            if t - self._fist_since >= cfg.fist_hold:
                self._fist = True
        else:
            self._fist_since = None

    def _update_three(self, reach, t):
        cfg = self.cfg
        if self._three:
            if min(reach[:3]) < cfg.three_ext * 0.85 or reach[3] > cfg.three_pinky_exit:
                self._three = False
                self._three_since = None
        elif min(reach[:3]) > cfg.three_ext and reach[3] < cfg.three_pinky:
            if self._three_since is None:
                self._three_since = t
            if t - self._three_since >= cfg.three_hold:
                self._three = True
        else:
            self._three_since = None

    def _second_fist(self, second) -> bool:
        cfg = self.cfg
        if second is None:
            self._second = False
            return False
        _, reach = pinch_features(second.world)
        if self._second:
            self._second = max(reach) <= cfg.fist_exit
        else:
            self._second = max(reach) < cfg.fist_enter
        return self._second

    def _stick(self, t):
        self._sticky_until = t + self.cfg.click_sticky
        self._sticky_acc = [0.0, 0.0]

    # --- pinch: click / drag / right click
    def _pinch_held(self, events, t, mx, my):
        cfg = self.cfg
        if self._pi > cfg.pinch_off_index:
            if self.mode == "drag":
                recent = [h for h in self._drag_hist if h[0] > t - cfg.fling_window]
                events.append(Event("drag_end", sum(h[1] for h in recent) / cfg.fling_window,
                                    sum(h[2] for h in recent) / cfg.fling_window))
            elif self.mode == "press":
                events.append(Event("click"))
            self._stick(t)
            self.mode = "move"
            return
        if self.mode == "press":
            # Cursor stays frozen so the click lands where it was aimed.
            self._acc[0] += mx
            self._acc[1] += my
            held = t - self._t0
            if math.hypot(*self._acc) >= cfg.drag_move:
                self.mode = "drag"
                self._drag_hist = []
                events.append(Event("drag_start"))
                events.append(Event("move", *self._acc))
            elif held >= cfg.right_hold:
                self.mode = "held"
                events.append(Event("right_click"))
            else:
                self.hold = _clamp01((held - cfg.right_hold_show) / (cfg.right_hold - cfg.right_hold_show))
        elif self.mode == "drag" and (mx or my):
            events.append(Event("move", mx, my))
            self._drag_hist.append((t, mx, my))
            if len(self._drag_hist) > 30:
                del self._drag_hist[:-30]

    # --- three fingers: swipe the page
    def _scroll_held(self, events, dx, dy):
        cfg = self.cfg
        if self.mode == "three":
            self._acc[0] += dx
            self._acc[1] += dy
            if math.hypot(*self._acc) >= cfg.scroll_start:
                self.mode = "scroll"
                self.scroll_axis = "y" if abs(self._acc[1]) >= abs(self._acc[0]) else "x"
                self._scroll_rem = 0.0
            return
        # The page follows the hand: hand up -> content up -> wheel down.
        sign = -1.0 if cfg.scroll_invert else 1.0
        amount = (dy if self.scroll_axis == "y" else -dx) * cfg.scroll_gain * sign
        if amount * self._scroll_rem < 0:      # direction reversed: drop leftover
            self._scroll_rem = 0.0
        self._scroll_rem += amount
        steps = int(self._scroll_rem / cfg.scroll_step)
        if steps:
            delta = steps * cfg.scroll_step
            self._scroll_rem -= delta
            if self.scroll_axis == "y":
                events.append(Event("scroll", 0, delta))
            else:
                events.append(Event("scroll", delta, 0))

    # --- two fists: zoom
    def _hands_apart(self, hand, second) -> float:
        """Distance between the two palms, in hand-lengths."""
        a, b = self._palm(hand.lm), self._palm(second.lm)
        return math.hypot(a[0] - b[0], a[1] - b[1]) / self._scale

    def _zoom_held(self, t, events, hand, second, second_fist):
        cfg = self.cfg
        if not self._fist or second is None or not second_fist:
            self.mode = "grab" if self._fist else "move"
            self._hold_until = t + cfg.resume_delay
            return
        apart = self._hands_apart(hand, second)
        change = apart - self._zoom_prev
        if abs(change) < cfg.zoom_deadzone:
            return
        self._zoom_prev = apart
        self._zoom_rem += change * cfg.zoom_gain
        steps = int(self._zoom_rem / cfg.zoom_step)
        if steps:
            delta = steps * cfg.zoom_step
            self._zoom_rem -= delta
            events.append(Event("zoom", 0, delta))

    def _hand_lost(self, events):
        if self.mode == "drag":
            events.append(Event("drag_end"))
        self.mode = "lost"
        self.progress = 0.0
        self.hold = 0.0
        self._seen = False
        self._lost_since = None
        self._prev = None
        self._scale = None
        self._pi = None
        self._last_t = None
        self._fist = False
        self._fist_since = None
        self._three = False
        self._three_since = None
        self._second = False
        self._sticky_until = 0.0
        self._fx.reset()
        self._fy.reset()
