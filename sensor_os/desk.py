"""The glass desk: one mode where the hands work the real desktop and the glass panels together.

Two closed hands, two meanings, and that is all there is to learn:
    pinch (thumb + index)   touch: click in a program, or press a button / open something on a panel
    fist                    grab: pick up a real window or a glass panel, move it, throw it
An open hand moves the pointer, as in the hologram mode.

Who handles a hand is decided once, at the moment it closes, by what is under it:
    a glass panel, a shelf tab or the + tab        the panels (space.py), until the hand opens again
    a fist over empty desktop                      the panels too: two fists there pulled apart = a new panel
    anything else                                  the desktop (holo.py): click, drag, grab a window...
While the panels hold a hand, the desktop engine ignores it, so nothing is clicked behind a panel.

Pure logic: update() returns (desktop commands, panel commands).
"""
import math

from .holo import HoloEngine
from .space import SpaceEngine

PALM = (0, 5, 9, 13, 17)


class DeskHand:
    """A hand as the panels see it, filled in from the desktop engine's tracking."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.lost_since = None
        self.closed = False
        self.just_closed = False
        self.just_opened = False
        self.point = (0.5, 0.5)
        self.size = 0.1
        self.pvel = (0.0, 0.0)
        self.closed_on_empty = None
        self.touch = False            # closed by a pinch (touch), not a fist (grab)
        self.trail = []

    def velocity(self, t: float, window: float = 0.12) -> tuple:
        recent = [p for p in self.trail if p[0] >= t - window - 1e-9]
        if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
            return 0.0, 0.0
        dt = recent[-1][0] - recent[0][0]
        return (recent[-1][1] - recent[0][1]) / dt, (recent[-1][2] - recent[0][2]) / dt


class GlassDesk:
    def __init__(self, holo: HoloEngine, space: SpaceEngine):
        self.holo, self.space = holo, space
        self.hands = [DeskHand(), DeskHand()]
        space.slots = self.hands                  # the panels read their hands from here
        self.claimed: set = set()                 # slots the panels are handling
        self._was_closed = [False, False]

    def update(self, hands: list, t: float) -> tuple:
        holo, space, cfg = self.holo, self.space, self.holo.cfg
        holo_cmds = holo.track(hands, t)
        lost = []
        for si, (hs, dh) in enumerate(zip(holo.slots, self.hands)):
            dh.just_closed = dh.just_opened = False
            if not (hs.active and hs.lost_since is None and hs.pts is not None):
                if dh.active:
                    lost.append(si)
                    self.claimed.discard(si)
                    dh.active = False
                self._was_closed[si] = False
                continue
            dh.active, dh.lost_since = True, None
            vx, vy = hs.velocity(t, 0.06)
            dh.pvel = (vx, vy)
            lead = cfg.space_lead
            dh.point = (hs.pos[0] + vx * lead, hs.pos[1] + vy * lead)    # the ring, a little ahead of the camera
            dh.trail.append((t, *dh.point))
            while dh.trail and t - dh.trail[0][0] > 0.25:
                dh.trail.pop(0)
            a, b = hs.pts[0], hs.pts[9]
            dh.size = max(math.hypot((b[0] - a[0]) * holo.aspect, b[1] - a[1]), 1e-4)

            closed = hs.pinched or hs.fist
            if si in self.claimed:
                dh.closed = closed
                if not closed:
                    dh.just_opened = True
            else:
                dh.closed = False
                if closed and not self._was_closed[si] and holo.owner != si and self._wants(hs, dh.point):
                    self.claimed.add(si)
                    dh.closed = dh.just_closed = True
                    dh.touch = hs.pinched and not hs.fist     # pinch = press it, fist = pick it up
            self._was_closed[si] = closed

        space_cmds = space.drive(t, lost)
        for si, dh in enumerate(self.hands):
            if dh.just_opened:
                self.claimed.discard(si)
        for si, hs in enumerate(holo.slots):
            hs.blocked = si in self.claimed
        holo_cmds += holo.act(t)
        return holo_cmds, space_cmds

    def _wants(self, hs, point) -> bool:
        """Should the panels take this hand that has just closed?"""
        if self.space.hidden:
            return False
        if self.space.hit(point).where != "none":
            return True
        empty = self.holo.probe is None or not self.holo.probe(*point)
        return hs.fist and not hs.pinched and empty       # a fist on bare desktop: maybe the start of a new panel

    def release_all(self) -> list:
        self.claimed.clear()
        self.space.release_all()
        for dh in self.hands:
            dh.reset()
        for hs in self.holo.slots:
            hs.blocked = False
        return self.holo.release_all()
