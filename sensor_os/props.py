"""Hologram props: 3D models floating over the desktop that the hands can turn, carry, stretch and throw.

Pure logic (numpy only). Positions are fractions of the screen; size is the diameter in screen heights.
Screen axes: x right, y down, z towards the viewer.
"""
import math
from dataclasses import dataclass, field

import numpy as np


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array(((1, 0, 0), (0, c, -s), (0, s, c)))


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array(((c, 0, s), (0, 1, 0), (-s, 0, c)))


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array(((c, -s, 0), (s, c, 0), (0, 0, 1)))


@dataclass
class Prop:
    id: int
    ref: str
    pos: tuple
    size: float
    R: np.ndarray = field(default_factory=lambda: _rx(-0.35) @ _ry(0.5))
    spin: bool = True
    held: bool = False
    value: float | None = None    # controls: the level shown on the gauge, 0..1
    info: tuple = ()              # controls: extra facts for the label (muted, cpu, ram, battery...)
    travel: float = 0.0           # controls: sideways hand travel of the current pinch

    @property
    def control(self):
        """"volume", "brightness", "media", "system", or None for a plain model."""
        return self.ref.split(":", 1)[1] if self.ref.startswith("control:") else None


DIALS = ("volume", "brightness")      # controls whose level the hand sets by turning them


class PropSpace:
    MAX = 6

    def __init__(self, cfg, aspect: float):
        self.cfg, self.aspect = cfg, aspect
        self.props: list[Prop] = []
        self.hover = None
        self.carried = None               # the model a hand is moving (not turning) right now
        self.events: list[tuple] = []     # (control, action) for the machine: see drain()
        self._next = 1

    # ---------------------------------------------------------------- content
    def add(self, ref: str, pos=None) -> int:
        n = len(self.props)
        pos = pos or (0.5 + 0.06 * (n % 3 - 1), 0.45 + 0.05 * (n % 2))
        prop = Prop(self._next, ref, pos, self.cfg.prop_size)
        if prop.control:
            if any(p.ref == ref for p in self.props):
                return next(p.id for p in self.props if p.ref == ref)     # one of each control is enough
            prop.size = self.cfg.control_size
            prop.R = _rx(-0.45)                 # a dial faces the viewer, tipped back a little
            prop.spin = prop.control == "system"
            if prop.control in DIALS:
                prop.value = 0.0
        self._next += 1
        self.props.append(prop)
        if len(self.props) > self.MAX:
            self.props.pop(0)
        return prop.id

    # ---------------------------------------------------------------- controls
    def drain(self) -> list:
        """What the hands asked of the machine since the last call: (control, level or action)."""
        out, self.events = self.events, []
        return out

    def sync(self, state: dict) -> None:
        """Show the machine's real state on the controls. A dial in the hand keeps the hand's value."""
        for p in self.props:
            c = p.control
            if c == "volume":
                if not p.held and state.get("volume") is not None:
                    p.value = state["volume"]
                p.info = (bool(state.get("muted")),)
            elif c == "brightness":
                if not p.held:
                    p.value = state.get("brightness")
            elif c == "system":
                p.value = state.get("cpu")
                p.info = (state.get("cpu"), state.get("ram"), state.get("battery"), state.get("plugged"))

    def get(self, pid):
        for p in self.props:
            if p.id == pid:
                return p
        return None

    def has(self, pid) -> bool:
        return self.get(pid) is not None

    def remove(self, pid) -> None:
        self.props = [p for p in self.props if p.id != pid]
        if self.carried == pid:
            self.carried = None

    def describe(self) -> list:
        """What is floating now, for JARVIS: names of the 3D models and controls."""
        names = {"volume": "volume dial", "brightness": "brightness dial", "media": "media control", "system": "system orb"}
        return [names.get(p.control, p.control) if p.control else "3D model " + p.ref.split(":")[-1].rsplit("\\", 1)[-1]
                for p in self.props]

    def clear(self) -> None:
        self._stash, self.props = self.props, []

    def restore(self) -> None:
        """Bring back what the last clear() put away."""
        self.props = getattr(self, "_stash", []) + self.props
        self._stash = []

    def hit(self, point):
        """Id of the front-most prop under a point, or None."""
        for p in reversed(self.props):
            if math.hypot((point[0] - p.pos[0]) * self.aspect, point[1] - p.pos[1]) <= p.size * 0.5:
                return p.id
        return None

    def set_hover(self, points) -> None:
        self.hover = next((h for h in (self.hit(pt) for pt in points) if h is not None), None)

    # ---------------------------------------------------------------- manipulation
    def hold(self, pid) -> None:
        p = self.get(pid)
        if p:
            p.held = True
            self.props.remove(p)          # bring to the front
            self.props.append(p)

    def release(self, pid) -> None:
        p = self.get(pid)
        if p:
            p.held = False
            if p.control == "media" and abs(p.travel) >= self.cfg.control_swipe:
                self.events.append(("media", "next" if p.travel > 0 else "prev"))
            p.travel = 0.0

    def tap(self, pid) -> None:
        p = self.get(pid)
        if not p:
            return
        if p.control == "volume":
            self.events.append(("volume", "mute"))
        elif p.control == "media":
            self.events.append(("media", "play"))
        elif p.control is None:
            p.spin = not p.spin

    def rotate(self, pid, dx: float, dy: float) -> None:
        """Turn it as if the hand were dragging its front surface. Turning a dial sets its level."""
        p = self.get(pid)
        if not p:
            return
        g = self.cfg.prop_rot_gain
        if p.control in DIALS:
            if p.value is None:
                return                                    # this machine cannot set it (no such device)
            d = dx * self.aspect - dy                     # right or up = more
            new = min(max(p.value + d * self.cfg.control_gain, 0.0), 1.0)
            p.R = _rz((new - p.value) * 4.0) @ p.R        # the dial turns only as far as the level moves
            if new != p.value:
                p.value = new
                self.events.append((p.control, new))
            return
        if p.control == "media":
            p.travel += dx * self.aspect
            p.R = _ry(dx * self.aspect * g) @ p.R
            return
        p.R = _ry(dx * self.aspect * g) @ _rx(-dy * g) @ p.R
        if p.control is None:
            p.spin = False

    def roll(self, pid, angle: float) -> None:
        p = self.get(pid)
        if p:
            p.R = _rz(angle) @ p.R

    def move(self, pid, dx: float, dy: float) -> None:
        p = self.get(pid)
        if p:              # it may be carried part way out of the screen: that is how it is thrown away
            p.pos = (min(max(p.pos[0] + dx, -0.25), 1.25), min(max(p.pos[1] + dy, -0.25), 1.25))

    # ---------------------------------------------------------------- throwing away: out of the screen
    OUT = 0.13             # centre this close to an edge (in radii) = about 40 % of the model is out

    def off_screen(self, pid) -> bool:
        """Carried far enough out of the screen that letting go throws it away (as with the glass panels)."""
        p = self.get(pid)
        if not p:
            return False
        rx, ry = p.size * 0.5 / self.aspect, p.size * 0.5
        x, y = p.pos
        return x > 1 - self.OUT * rx or x < self.OUT * rx or y > 1 - self.OUT * ry or y < self.OUT * ry

    def push_out(self, pid, hand, dt: float, slide: float = 0.25) -> None:
        """The pointer stops at the screen's edge: a model held by its inner side would never get far enough
        out, so while the hand pushes at an edge the model slides on out until the hand holds its middle."""
        p = self.get(pid)
        if not p or dt <= 0:
            return
        f = 1.0 - math.exp(-dt / slide)
        x, y = p.pos
        if (hand[0] >= 0.995 and x < hand[0]) or (hand[0] <= 0.005 and x > hand[0]):
            x += (hand[0] - x) * f
        if (hand[1] >= 0.995 and y < hand[1]) or (hand[1] <= 0.005 and y > hand[1]):
            y += (hand[1] - y) * f
        p.pos = (x, y)

    def come_back(self, pid) -> None:
        """Let go still mostly on the screen: it settles fully back on it."""
        p = self.get(pid)
        if p:
            p.pos = (min(max(p.pos[0], 0.0), 1.0), min(max(p.pos[1], 0.0), 1.0))

    def size(self, pid) -> float:
        p = self.get(pid)
        return p.size if p else 0.0

    def set_size(self, pid, size: float) -> None:
        p = self.get(pid)
        if p:
            p.size = min(max(size, 0.12), 1.4)

    def tick(self, dt: float) -> None:
        if dt <= 0:
            return
        dt = min(dt, 0.1)
        step = _ry(dt * self.cfg.prop_spin)
        for p in self.props:
            if p.spin and not p.held:
                if p.control == "system":                 # a busy machine spins faster
                    p.R = _ry(dt * (0.25 + 5.0 * (p.value or 0.0))) @ p.R
                else:
                    p.R = step @ p.R

    def view(self) -> list:
        """state: "bin" = carried out of the screen far enough that letting go throws it away (red)."""
        return [(p.id, p.ref, p.pos[0], p.pos[1], p.size, p.R.copy(),
                 ("bin" if p.id == self.carried and self.off_screen(p.id) else "held") if p.held
                 else "hover" if p.id == self.hover else "", p.value, p.info) for p in self.props]
