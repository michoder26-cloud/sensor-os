"""Stage mode: your real windows float over the mirrored camera image and you handle them directly.

Pure logic, no Qt and no Windows calls, so it can be tested with synthetic hands.

Coordinates
    frame   mirrored camera image, 0..1
    stage   the full screen, 0..1 (x right, y down)
    screen  physical pixels of the primary monitor

The camera image is shown full screen (aspect-fill, centre crop), and a hand is drawn
exactly where it appears in that image, so what you see is what you grab.

Gestures on the stage
    pinch a card briefly            -> focus that window and leave the stage
    pinch a card and move           -> move the window
    pinch one card with both hands  -> pull apart / push together to resize (nearly full = maximize)
    flick a dragged card and let go -> minimize it (it goes to the tray at the bottom)
    pinch a tray tile               -> tap = restore and focus, drag up = restore in place
    two fists                       -> leave the stage
"""
import math
from dataclasses import dataclass, field, replace
from itertools import permutations

from .config import Config
from .filters import OneEuroFilter
from .gestures import WRIST, Hand, pinch_features, pointer_point

TRAY_TOP = 0.905         # stage y where the tray strip begins
TRAY_TILE = (0.10, 0.062)
TRAY_GAP = 0.008
TRAY_Y = 0.917


class CameraMap:
    """Mirrored camera frame <-> stage, matching how the stage view draws the camera."""

    def __init__(self, cam_aspect: float, screen_aspect: float, zoom: float):
        if cam_aspect > screen_aspect:
            fw, fh = screen_aspect / cam_aspect, 1.0
        else:
            fw, fh = 1.0, cam_aspect / screen_aspect
        self.fw, self.fh = fw * zoom, fh * zoom
        self.u0, self.v0 = 0.5 - self.fw / 2, 0.5 - self.fh / 2

    @property
    def crop(self) -> tuple:
        """Visible part of the camera frame: (u0, v0, u1, v1)."""
        return self.u0, self.v0, self.u0 + self.fw, self.v0 + self.fh

    def to_stage(self, u: float, v: float) -> tuple:
        return (u - self.u0) / self.fw, (v - self.v0) / self.fh

    def to_frame(self, x: float, y: float) -> tuple:
        return self.u0 + x * self.fw, self.v0 + y * self.fh


class DesktopMap:
    """The real desktop drawn as a scaled-down map in the middle of the stage."""

    def __init__(self, screen_w: int, screen_h: int, scale: float):
        self.w, self.h, self.s = screen_w, screen_h, scale

    def to_stage(self, rect) -> tuple:
        l, t, r, b = rect
        s = self.s
        return (0.5 + (l / self.w - 0.5) * s, 0.5 + (t / self.h - 0.5) * s,
                (r - l) / self.w * s, (b - t) / self.h * s)

    def to_screen(self, rect) -> tuple:
        x, y, w, h = rect
        s = self.s
        l = ((x - 0.5) / s + 0.5) * self.w
        t = ((y - 0.5) / s + 0.5) * self.h
        return round(l), round(t), round(l + w / s * self.w), round(t + h / s * self.h)

    @property
    def full(self) -> tuple:
        return 0.5 - self.s / 2, 0.5 - self.s / 2, self.s, self.s


@dataclass
class Card:
    id: int
    title: str
    rect: tuple                  # (x, y, w, h) in stage units
    minimized: bool = False
    source: tuple | None = None  # thumbnail source crop, passed through for the view


def _inside(p, rect) -> bool:
    x, y, w, h = rect
    return x <= p[0] <= x + w and y <= p[1] <= y + h


def tray_rects(n: int) -> list:
    w, h = TRAY_TILE
    total = n * w + max(0, n - 1) * TRAY_GAP
    x0 = 0.5 - total / 2
    return [(x0 + i * (w + TRAY_GAP), TRAY_Y, w, h) for i in range(n)]


class HandSlot:
    """One tracked hand on the stage: filtered pinch point, pinch state, fist, recent motion."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.fx = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self.fy = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self.reset()

    def reset(self):
        self.active = False
        self.anchor = (0.5, 0.5)   # wrist, for matching hands between frames
        self.anchor_t = None
        self.vel = (0.0, 0.0)      # wrist velocity, stage units/s, to predict where the hand is going
        self.pos = (0.5, 0.5)      # pinch point on the stage
        self.pinch = None
        self.last_t = None
        self.pinched = False
        self.open_t = None         # last time thumb and index were clearly apart
        self.open_level = None     # how far apart they are when this hand is relaxed
        self.fist = False
        self.just_pinched = False
        self.just_released = False
        self.lost_since = None
        self.hist = []             # (t, x, y) while pinched
        self.card = None           # id of the card this hand is holding
        self.hover = None
        self.fx.reset()
        self.fy.reset()

    def update(self, hand: Hand, t: float, cmap: CameraMap):
        cfg = self.cfg
        lm = hand.lm
        new = cmap.to_stage(*lm[WRIST])
        if self.active and self.anchor_t is not None and t > self.anchor_t:
            dt = t - self.anchor_t
            v = ((new[0] - self.anchor[0]) / dt, (new[1] - self.anchor[1]) / dt)
            self.vel = (self.vel[0] + 0.5 * (v[0] - self.vel[0]), self.vel[1] + 0.5 * (v[1] - self.vel[1]))
        else:
            self.vel = (0.0, 0.0)
        self.anchor, self.anchor_t = new, t
        px, py = cmap.to_stage(*pointer_point(lm, cfg.pointer_anchor))
        self.pos = (min(max(self.fx(px, t), 0.0), 1.0), min(max(self.fy(py, t), 0.0), 1.0))   # never off the screen
        p, reach = pinch_features(hand.world)
        dt = t - self.last_t if self.last_t is not None else 0.0
        self.last_t = t
        a = 1.0 - math.exp(-dt / cfg.pinch_tau) if dt > 0 else 1.0
        self.pinch = p if self.pinch is None else self.pinch + a * (p - self.pinch)
        self.fist = max(reach) < (cfg.fist_exit if self.fist else cfg.fist_enter)
        on, off = self.thresholds(dt)
        if self.pinch > off:
            self.open_t = t                         # apart: past the point where a pinch lets go
        was = self.pinched
        if was:
            self.pinched = self.pinch <= off
        else:
            # Only a deliberate pinch counts: fingers that were clearly apart a moment ago and closed
            # quickly. Fingers that slowly curl together on their own (a resting hand) do not click.
            quick = self.open_t is not None and t - self.open_t <= cfg.pinch_arm_time
            self.pinched = (self.pinch < on and quick and reach[0] > cfg.pinch_reach_min and not self.fist)
        self.just_pinched = self.pinched and not was
        self.just_released = was and not self.pinched
        if self.pinched:
            self.hist.append((t, *self.pos))
            if len(self.hist) > 40:
                del self.hist[:-40]
        self.active = True
        self.lost_since = None

    def thresholds(self, dt: float) -> tuple:
        """(pinch at, let go at) for this hand. They follow how far apart this hand's thumb and index
        are when it is relaxed: measured on a real hand, a relaxed open hand sat at 0.56, right on the
        fixed let-go line (0.555), so pinches were barely released and turned into holds."""
        cfg = self.cfg
        if not self.fist and self.pinch is not None:
            if self.open_level is None:
                self.open_level = max(self.pinch, cfg.pinch_open_floor)
            elif self.pinch > self.open_level:
                self.open_level += 0.3 * (self.pinch - self.open_level)       # follows an opening hand quickly
            else:
                self.open_level -= cfg.pinch_open_decay * dt                   # forgets slowly
            self.open_level = max(self.open_level, cfg.pinch_open_floor)
        level = self.open_level if self.open_level is not None else 1.0
        on = min(cfg.pinch_on_index, 0.55 * level)
        off = min(cfg.pinch_off_index, max(on + 0.06, 0.75 * level))
        return on, off

    def predict(self, t: float) -> tuple:
        if self.anchor_t is None:
            return self.anchor
        dt = min(t - self.anchor_t, 0.2)
        return self.anchor[0] + self.vel[0] * dt, self.anchor[1] + self.vel[1] * dt

    def speed(self, t: float, window: float) -> tuple:
        """Velocity of the pinch point over the last `window` seconds, stage units/s."""
        recent = [h for h in self.hist if h[0] >= t - window - 1e-9]
        if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
            return 0.0, 0.0
        dt = recent[-1][0] - recent[0][0]
        return (recent[-1][1] - recent[0][1]) / dt, (recent[-1][2] - recent[0][2]) / dt


@dataclass
class Session:
    """A card being held by one or two hands."""
    card: int
    t0: float
    orig: tuple
    start: tuple
    tray: bool = False
    offsets: dict = field(default_factory=dict)   # slot index -> pinch point minus card origin
    moved: bool = False
    resized: bool = False
    pair: tuple | None = None                     # (d0, midpoint0, rect0) while two hands hold it


class StageEngine:
    def __init__(self, cfg: Config, cmap: CameraMap, dmap: DesktopMap):
        self.cfg, self.cmap, self.dmap = cfg, cmap, dmap
        self.slots = [HandSlot(cfg), HandSlot(cfg)]
        self.cards: list[Card] = []
        self.sessions: dict[int, Session] = {}
        self.pending: dict[int, tuple] = {}   # id -> (card, until): local result shown until Windows catches up
        self.exit_since = None
        self.exit_progress = 0.0
        self.done = False

    # ------------------------------------------------------------------ cards
    def reset(self, cards: list[Card]) -> None:
        self.cards = list(cards)
        for s in self.slots:
            s.reset()
        self.sessions.clear()
        self.pending.clear()
        self.exit_since = None
        self.exit_progress = 0.0
        self.done = False

    def sync(self, cards: list[Card], t: float) -> None:
        """Refresh from the real windows, keeping cards that are held or were just changed."""
        held = {c.id: c for c in self.cards if c.id in self.sessions}
        for cid, (card, until) in list(self.pending.items()):
            if t >= until:
                del self.pending[cid]
        out = []
        for c in cards:
            if c.id in held:
                continue
            if c.id in self.pending:
                c = replace(c, rect=self.pending[c.id][0].rect, minimized=self.pending[c.id][0].minimized)
            out.append(c)
        self.cards = list(held.values()) + out

    def card(self, cid):
        for c in self.cards:
            if c.id == cid:
                return c
        return None

    def _tray(self) -> list:
        return [c for c in self.cards if c.minimized]

    def _hit(self, p):
        """(card, is_tray) under point p; tray tiles first, then cards front to back."""
        tray = self._tray()
        for c, r in zip(tray, tray_rects(len(tray))):
            if _inside(p, r):
                return c, True
        for c in self.cards:
            if not c.minimized and _inside(p, c.rect):
                return c, False
        return None, False

    # ------------------------------------------------------------------ hands
    def _match(self, hands: list[Hand], t: float):
        """Assign detected hands to the two slots so a hand keeps its identity between frames."""
        anchors = [self.cmap.to_stage(*h.lm[WRIST]) for h in hands[:2]]
        best, best_cost = None, None
        for perm in permutations(range(2), len(anchors)):
            cost = 0.0
            for hi, si in enumerate(perm):
                s = self.slots[si]
                cost += math.dist(anchors[hi], s.predict(t)) if s.active else 0.35
            if best_cost is None or cost < best_cost:
                best, best_cost = perm, cost
        used = set()
        for hi, si in enumerate(best or ()):
            self.slots[si].update(hands[hi], t, self.cmap)
            used.add(si)
        lost = []
        for si, s in enumerate(self.slots):
            if si in used:
                continue
            s.just_pinched = s.just_released = False
            if s.active:
                if s.lost_since is None:
                    s.lost_since = t
                if t - s.lost_since >= self.cfg.lost_grace:
                    lost.append(si)
        return lost

    # ------------------------------------------------------------------ main
    def update(self, hands: list[Hand], t: float) -> list[tuple]:
        """Returns commands: (focus, id) (move, id, rect) (resize, id, rect) (maximize, id)
        (minimize, id) (restore, id) (exit,). Rects are screen pixels (l, t, r, b)."""
        cfg = self.cfg
        cmds: list[tuple] = []
        if self.done:
            return cmds
        lost = self._match(hands, t)

        for si in lost:                      # a hand vanished: drop whatever it held, untouched
            s = self.slots[si]
            if s.card is not None:
                self._cancel(s.card)
            s.reset()

        # Two fists: leave the stage
        a, b = self.slots
        if a.active and b.active and a.fist and b.fist and not self.sessions:
            if self.exit_since is None:
                self.exit_since = t
            self.exit_progress = min(1.0, (t - self.exit_since) / cfg.stage_exit_hold)
            if self.exit_progress >= 1.0:
                self.done = True
                return [("exit",)]
        else:
            self.exit_since = None
            self.exit_progress = 0.0

        for si, s in enumerate(self.slots):
            if s.just_pinched:
                self._grab(si, s, t)
        for si, s in enumerate(self.slots):
            if s.just_released and s.card is not None:
                cmds += self._release(si, s, t)
        for sess in list(self.sessions.values()):
            self._follow(sess)
        for s in self.slots:
            s.hover = None
            if s.active and s.card is None:
                c, _ = self._hit(s.pos)
                s.hover = c.id if c else None
        if any(c[0] == "exit" for c in cmds):
            self.done = True
        return cmds

    def _grab(self, si, s, t):
        c, tray = self._hit(s.pos)
        if c is None:
            return
        sess = self.sessions.get(c.id)
        if sess is None:
            rect = tray_rects(len(self._tray()))[self._tray().index(c)] if tray else c.rect
            sess = Session(c.id, t, c.rect, s.pos, tray=tray)
            if tray:
                sess.tile = rect
            self.sessions[c.id] = sess
            self.cards.remove(c)             # bring it to the front
            self.cards.insert(0, c)
        else:                                 # second hand on the same card: two-hand resize
            others = [i for i in sess.offsets if i != si]
            if not others or sess.tray:
                return
            o = self.slots[others[0]]
            sess.pair = (max(math.dist(s.pos, o.pos), 1e-3), self._mid(s.pos, o.pos), c.rect)
        origin = sess.tile if sess.tray else c.rect
        sess.offsets[si] = (s.pos[0] - origin[0], s.pos[1] - origin[1])
        s.card = c.id

    @staticmethod
    def _mid(p, q):
        return (p[0] + q[0]) / 2, (p[1] + q[1]) / 2

    def _follow(self, sess: Session):
        cfg = self.cfg
        c = self.card(sess.card)
        holders = [i for i in sess.offsets]
        lead = self.slots[holders[0]]
        if math.dist(lead.pos, sess.start) > cfg.stage_tap_move:
            sess.moved = True
        if sess.tray:
            ox, oy = sess.offsets[holders[0]]
            sess.tile = (lead.pos[0] - ox, lead.pos[1] - oy, *TRAY_TILE)
            return
        if sess.pair and len(holders) == 2:
            p, q = self.slots[holders[0]].pos, self.slots[holders[1]].pos
            d0, m0, r0 = sess.pair
            k = math.dist(p, q) / d0
            k = max(k, cfg.stage_min_size / max(r0[2], 1e-6))
            w, h = r0[2] * k, r0[3] * k
            m = self._mid(p, q)
            cx = r0[0] + r0[2] / 2 + (m[0] - m0[0])
            cy = r0[1] + r0[3] / 2 + (m[1] - m0[1])
            c.rect = (cx - w / 2, cy - h / 2, w, h)
            if abs(k - 1) > 0.05:
                sess.resized = True
            return
        ox, oy = sess.offsets[holders[0]]
        c.rect = (lead.pos[0] - ox, lead.pos[1] - oy, c.rect[2], c.rect[3])

    def _release(self, si, s, t) -> list:
        cfg = self.cfg
        sess = self.sessions[s.card]
        c = self.card(s.card)
        s.card = None
        del sess.offsets[si]
        if sess.offsets:                      # the other hand keeps holding: carry on as a one-hand drag
            sess.pair = None
            oi = next(iter(sess.offsets))
            o = self.slots[oi]
            sess.offsets[oi] = (o.pos[0] - c.rect[0], o.pos[1] - c.rect[1])
            return []
        del self.sessions[c.id]
        held = t - sess.t0
        cmds = []
        if sess.tray:
            if sess.moved and s.pos[1] < TRAY_TOP:
                c.minimized = False
                cmds.append(("restore", c.id))
            elif not sess.moved and held <= cfg.stage_tap_time:
                c.minimized = False
                cmds += [("restore", c.id), ("focus", c.id), ("exit",)]
        elif sess.resized:
            if c.rect[2] >= cfg.stage_max_frac * self.dmap.s or c.rect[3] >= cfg.stage_max_frac * self.dmap.s:
                c.rect = self.dmap.full
                cmds.append(("maximize", c.id))
            else:
                cmds.append(("resize", c.id, self.dmap.to_screen(c.rect)))
        elif sess.moved:
            vx, vy = s.speed(t, cfg.fling_window)
            if math.hypot(vx, vy) >= cfg.stage_fling:
                c.rect = sess.orig
                c.minimized = True
                cmds.append(("minimize", c.id))
            else:
                cmds.append(("move", c.id, self.dmap.to_screen(c.rect)))
        elif held <= cfg.stage_tap_time:
            c.rect = sess.orig
            cmds += [("focus", c.id), ("exit",)]
        else:
            c.rect = sess.orig
        if cmds and cmds[0][0] != "focus":
            self.pending[c.id] = (replace(c), t + 1.0)
        return cmds

    def _cancel(self, cid):
        sess = self.sessions.pop(cid, None)
        if sess is None:
            return
        c = self.card(cid)
        if c is not None:
            c.rect = sess.orig
        for i in sess.offsets:
            self.slots[i].card = None

    # ------------------------------------------------------------------ view
    def view(self) -> dict:
        held = set(self.sessions)
        hovered = {s.hover for s in self.slots if s.hover is not None}

        def state(cid):
            return "held" if cid in held else "hover" if cid in hovered else ""

        cards = [(c.id, c.title, c.rect, state(c.id), c.source) for c in self.cards if not c.minimized]
        tray_cards = self._tray()
        tray = []
        for c, r in zip(tray_cards, tray_rects(len(tray_cards))):
            sess = self.sessions.get(c.id)
            tray.append((c.id, c.title, sess.tile if sess and sess.tray else r, state(c.id)))
        hands = [(s.pos[0], s.pos[1], s.pinched, s.card is not None or s.hover is not None, s.fist)
                 for s in self.slots if s.active]
        return {"cards": cards, "tray": tray, "hands": hands, "exit": self.exit_progress}


class SummonDetector:
    """Both hands raised and open for a moment: open the stage."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.since = None
        self.armed = True
        self.progress = 0.0

    def update(self, hands: list[Hand], t: float) -> bool:
        cfg = self.cfg
        both_open = len(hands) >= 2 and all(min(pinch_features(h.world)[1]) > cfg.open_reach for h in hands[:2])
        if not both_open:
            self.since, self.armed, self.progress = None, True, 0.0
            return False
        if not self.armed:
            self.progress = 0.0
            return False
        if self.since is None:
            self.since = t
        self.progress = min(1.0, (t - self.since) / cfg.summon_hold)
        if self.progress >= 1.0:
            self.armed, self.since, self.progress = False, None, 0.0
            return True
        return False
