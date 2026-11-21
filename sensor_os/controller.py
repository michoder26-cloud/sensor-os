"""Turns gesture events into real mouse and keyboard input, on any OS.

The OS part is an `io` object from sensor_os.desktop (make_input()): cursor(), set_cursor(),
button(), click(), wheel(), zoom(), on_title_bar(), snap(), screen(), bounds().
"""
import math
import threading
import time


class CursorMover(threading.Thread):
    """Feeds cursor movement at ~120 Hz.

    The camera only delivers 30-60 updates a second; spreading each update over
    the time until the next one makes the cursor glide instead of stepping.
    Coexists with the physical mouse.
    """

    def __init__(self, io, hz: int = 120):
        super().__init__(daemon=True)
        self.io = io
        self._period = 1.0 / hz
        self._lock = threading.Lock()        # pending movement
        self._apply_lock = threading.Lock()  # cursor position
        self._rx = self._ry = 0.0
        self._time_left = 0.0
        self._interval = 1 / 30
        self._last_add = None
        self._pos = None
        self._last_set = None
        self._running = True
        self.vx, self.vy, self.vw, self.vh = io.bounds()
        self.vw, self.vh = max(self.vw, 2), max(self.vh, 2)

    def add(self, dx: float, dy: float) -> None:
        now = time.perf_counter()
        with self._lock:
            if self._last_add is not None:
                gap = now - self._last_add
                if 0.004 < gap < 0.1:
                    self._interval += 0.2 * (gap - self._interval)
            self._last_add = now
            self._rx += dx
            self._ry += dy
            self._time_left = self._interval

    def goto(self, x: float, y: float) -> None:
        """Glide to an absolute position."""
        cx, cy = self.io.cursor()
        with self._lock:
            dx, dy = x - (cx + self._rx), y - (cy + self._ry)
        if dx or dy:
            self.add(dx, dy)

    def flush(self) -> None:
        """Apply everything still pending right now (before a click)."""
        with self._lock:
            dx, dy, self._rx, self._ry = self._rx, self._ry, 0.0, 0.0
        if dx or dy:
            self._apply(dx, dy)

    def run(self):
        last = time.perf_counter()
        while self._running:
            time.sleep(self._period)
            now = time.perf_counter()
            dt, last = now - last, now
            with self._lock:
                if not (self._rx or self._ry):
                    continue
                frac = 1.0 if self._time_left <= dt else dt / self._time_left
                dx, dy = self._rx * frac, self._ry * frac
                self._rx -= dx
                self._ry -= dy
                self._time_left -= dt
            self._apply(dx, dy)

    def _apply(self, dx: float, dy: float) -> None:
        with self._apply_lock:
            cx, cy = self.io.cursor()
            # If something else moved the cursor (the real mouse), continue from there.
            if (self._pos is None or self._last_set is None
                    or abs(cx - self._last_set[0]) > 1 or abs(cy - self._last_set[1]) > 1):
                self._pos = [float(cx), float(cy)]
            self._pos[0] = min(max(self._pos[0] + dx, self.vx), self.vx + self.vw - 1)
            self._pos[1] = min(max(self._pos[1] + dy, self.vy), self.vy + self.vh - 1)
            ix, iy = round(self._pos[0]), round(self._pos[1])
            if (ix, iy) != (round(cx), round(cy)):
                self.io.set_cursor(ix, iy)
            self._last_set = (ix, iy)

    def stop(self):
        self._running = False


class Controller:
    """Maps gesture events to real input."""

    def __init__(self, io, fling_speed: float = 4.5):
        self.io = io
        self.screen_w, self.screen_h = io.screen()
        self.fling_speed = fling_speed
        self._left_down = False
        self._title_drag = False
        self._mover = CursorMover(io)
        self._mover.start()

    def goto(self, x: float, y: float) -> None:
        """Move the cursor to a point given as 0..1 of the primary screen."""
        self._mover.goto(min(max(x, 0.0), 1.0) * (self.screen_w - 1), min(max(y, 0.0), 1.0) * (self.screen_h - 1))

    def handle(self, ev) -> str | None:
        """Returns "fling" when a window was thrown, otherwise None."""
        kind = ev.kind
        if kind == "move":
            self._mover.add(ev.x * self.screen_h, ev.y * self.screen_h)
        elif kind in ("click", "right_click"):
            self._mover.flush()
            self.io.click("left" if kind == "click" else "right")
        elif kind == "drag_start":
            self._mover.flush()
            self._title_drag = self.io.on_title_bar()
            self.io.button("left", True)
            self._left_down = True
        elif kind == "drag_end":
            self._mover.flush()
            self.release()
            thrown = self._title_drag and math.hypot(ev.x, ev.y) >= self.fling_speed
            self._title_drag = False
            if thrown:
                time.sleep(0.05)       # let the window finish being dragged first
                if abs(ev.x) >= abs(ev.y):
                    direction = "right" if ev.x > 0 else "left"
                else:
                    direction = "down" if ev.y > 0 else "up"
                if self.io.snap(direction):
                    return "fling"
        elif kind == "zoom":
            self.io.zoom(ev.y)
        elif kind == "scroll":
            self.io.wheel(ev.x, ev.y)
        return None

    def release(self) -> None:
        if self._left_down:
            self.io.button("left", False)
            self._left_down = False

    def close(self) -> None:
        self.release()
        self._mover.stop()


class DryRunController:
    """Prints events instead of sending them."""

    def goto(self, x: float, y: float) -> None:
        pass

    def handle(self, ev) -> str | None:
        if ev.kind != "move":
            print(f"[dry-run] {ev.kind} x={ev.x:.1f} y={ev.y:.1f}", flush=True)
        return None

    def release(self) -> None:
        pass

    def close(self) -> None:
        pass
