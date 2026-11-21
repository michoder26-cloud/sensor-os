"""sensor-OS: control Windows with your hand through a webcam.

Usage:
    python main.py               # control the real mouse, with the HUD
    python main.py --dry-run     # show what it detects but do not touch the mouse
    python main.py --calibrate   # redo the 10-second hand setup
    python main.py --debug       # also show the camera window
    python main.py --stage       # start on the stage (real windows over the camera image)
    python main.py --no-hud      # no HUD
    python main.py --camera 1    # use another webcam

Hotkeys (work anywhere):
    Ctrl+Alt+Q  turn control on / off
    Ctrl+Alt+C  hand setup (fit the pinch to your hand)
    Ctrl+Alt+M  switch between hologram mode (hands are pointers) and wrist mode (air trackpad)
    Ctrl+Alt+S  open / close the stage (real windows over the camera image)
    Ctrl+Alt+H  show / hide the gesture help
    Ctrl+Alt+X  quit
"""
import argparse
import json
import queue
from pathlib import Path
import sys
import threading
import time
import traceback

import cv2

from sensor_os import calibrate, desktop, hub_data, preview, sound
from sensor_os.camera import Camera
from sensor_os.config import Config
from sensor_os.controller import Controller, DryRunController
from sensor_os.desktop import hotkey_down, make_dpi_aware
from sensor_os.gestures import Event, GestureEngine
from sensor_os.holo import HoloEngine, next_to_pull
from sensor_os.hub import Hub
from sensor_os.props import PropSpace
from sensor_os.sysctl import SystemControls
from sensor_os.shared import Shared
from sensor_os.desk import GlassDesk
from sensor_os.space import KIND_WORDS, SpaceEngine, describe_scene, initial_panels, put_away_target
from sensor_os.stage import CameraMap, Card, DesktopMap, StageEngine, SummonDetector
from sensor_os.head import HeadTracker
from sensor_os.tracker import HandTracker
from sensor_os.i18n import tr


VK_A, VK_C, VK_G, VK_H, VK_J, VK_M, VK_Q, VK_S, VK_X = "a", "c", "g", "h", "j", "m", "q", "s", "x"     # Ctrl+Alt+<letter>
SPACE_FILE = Path(__file__).resolve().parent / "space.json"     # the panels you made, kept between runs
STOP_FLAG = Path(__file__).resolve().parent / "stop.flag"


def parse_args():
    p = argparse.ArgumentParser(description="Control Windows with hand gestures.")
    p.add_argument("--dry-run", action="store_true", help="detect gestures but do not move the mouse")
    p.add_argument("--calibrate", action="store_true", help="redo the hand setup")
    p.add_argument("--skip-calibrate", action="store_true", help="do not run the first-time hand setup")
    p.add_argument("--debug", action="store_true", help="show the camera window")
    p.add_argument("--stage", action="store_true", help="open the stage right away")
    p.add_argument("--mode", choices=("holo", "wrist", "space"),
                   help="holo = the glass desk: real windows and glass panels (default; 'space' means the same), "
                        "wrist = air trackpad")
    p.add_argument("--no-hud", action="store_true", help="run without the on-screen HUD")
    p.add_argument("--camera", type=int, default=0, help="webcam index (default 0)")
    p.add_argument("--autostart", choices=("on", "off"), help="start sensor-OS when you log in (on) or not (off)")
    p.add_argument("--max-seconds", type=float, default=0, help="stop after N seconds (for testing)")
    return p.parse_args()


class Pipeline(threading.Thread):
    """camera -> hand tracker -> gesture engine -> Windows input, on its own thread."""

    def __init__(self, args, cfg: Config, shared: Shared):
        super().__init__(daemon=True)
        self.args, self.cfg, self.shared = args, cfg, shared
        self._stop_evt = threading.Event()
        self._seq = -1
        self._fps = 0.0
        self._frames = 0
        self._fps_t0 = time.perf_counter()
        self._frame = None

    def stop(self):
        self._stop_evt.set()

    def stopped(self) -> bool:
        return self._stop_evt.is_set()

    def run(self):
        try:
            self._run()
        except Exception as exc:  # report instead of dying silently on a thread
            traceback.print_exc()
            self.shared.update(error=str(exc))
        finally:
            self.shared.update(finished=True)

    def _next(self):
        """Blocks for the next camera frame. Returns (hands, time)."""
        while not self.stopped():
            self._seq, frame = self.camera.read(self._seq, timeout=0.5)
            if frame is None:
                continue
            frame = cv2.flip(frame, 1)   # mirror: moving your hand right moves things right
            now = time.perf_counter()
            hands = self.tracker.process(frame, now)
            self._frame = frame
            self._stamp = self.camera.stamp
            self._frames += 1
            if now - self._fps_t0 >= 0.5:
                self._fps = self._frames / (now - self._fps_t0)
                self._frames, self._fps_t0 = 0, now
                self.shared.update(fps=self._fps)
            return hands, now
        return [], time.perf_counter()

    def _next_hand(self):
        hands, now = self._next()
        hand = hands[0] if hands else None
        self.shared.update(lm=hand.lm if hand else None, mode="wake" if hand else "lost")
        return hand, now

    def _holo_command(self, cmd, controller, sw, sh, dry_run: bool):
        """Carry out one HoloEngine command. Returns "click" when the HUD should flash."""
        kind = cmd[0]
        if kind == "goto":
            controller.goto(cmd[1], cmd[2])
        elif kind in ("click", "right_click", "drag_start"):
            controller.handle(Event(kind))
            return "click" if kind == "click" else None
        elif kind == "drag_end":
            return "click" if controller.handle(Event("drag_end", cmd[1], cmd[2])) == "fling" else None
        elif kind == "scroll":
            controller.handle(Event("scroll", cmd[1], cmd[2]))
        elif dry_run:
            if kind != "scale":
                print(f"[dry-run] holo {cmd}", flush=True)
        elif kind == "scale_start":
            hwnd = desktop.window_at(cmd[1] * (sw - 1), cmd[2] * (sh - 1))
            rect = desktop.frame_bounds(hwnd) if hwnd else None
            self._scaling = (hwnd, rect) if rect else None
        elif kind == "scale" and self._scaling:
            hwnd, (l, t, r, b) = self._scaling
            cfg = self.cfg
            w = min(max((r - l) * cmd[1], cfg.holo_min_w), sw)
            h = min(max((b - t) * cmd[1], cfg.holo_min_h), sh)
            cx, cy = (l + r) / 2 + cmd[2] * sw, (t + b) / 2 + cmd[3] * sh
            desktop.place(hwnd, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
        elif kind == "scale_end":
            self._scaling = None
        elif kind == "grab_start":
            hwnd = desktop.window_at(cmd[1] * (sw - 1), cmd[2] * (sh - 1))
            rect = desktop.frame_bounds(hwnd) if hwnd else None
            self._grabbed = (hwnd, rect) if rect else None
        elif kind == "pull_start":
            self._grabbed = self._pull(cmd[1] * (sw - 1), cmd[2] * (sh - 1), sw, sh)
        elif kind == "grab_move" and self._grabbed:
            hwnd, (l, t, r, b) = self._grabbed
            dx, dy = cmd[1] * sw, cmd[2] * sh
            desktop.place(hwnd, (l + dx, t + dy, r + dx, b + dy))
        elif kind == "grab_end":
            grabbed, self._grabbed = self._grabbed, None
            if grabbed and (cmd[1] ** 2 + cmd[2] ** 2) ** 0.5 >= self.cfg.fling_speed:
                desktop.minimize(grabbed[0])           # thrown away
                return "click"
        elif kind == "hub_window":
            desktop.focus(cmd[1])
        elif kind == "hub_open":
            try:
                hub_data.open_path(cmd[1])
            except OSError as exc:
                print(f"Could not open {cmd[1]}: {exc}", flush=True)
        elif kind == "clear":
            self._put_away = [info.hwnd for info in desktop.list_windows() if not info.minimized]
            for hwnd in self._put_away:
                desktop.minimize(hwnd)
            return "click"
        elif kind == "unclear":
            for hwnd in reversed(getattr(self, "_put_away", [])):     # back to front, so the order comes back too
                desktop.restore(hwnd)
            self._put_away = []
            return "click"
        return None

    def _pull(self, x, y, sw, sh):
        """Claw: bring the next window to the hand at pixel (x, y). Returns (hwnd, rect) to carry, or None."""
        now = time.perf_counter()
        if now - getattr(self, "_pull_t", -1e9) > 8.0:
            self._pull_seen = []                     # a new run of claws starts from the front again
        self._pull_t = now
        infos = desktop.list_windows()
        front = next((i.hwnd for i in infos if not i.minimized), None)
        hwnd = next_to_pull([i.hwnd for i in infos], front, desktop.window_at(x, y), self._pull_seen)
        info = next((i for i in infos if i.hwnd == hwnd), None)
        if info is None:
            return None
        w = min(info.rect[2] - info.rect[0], sw)
        h = min(info.rect[3] - info.rect[1], sh)
        left = min(max(x - w / 2, 0), sw - w)        # centred on the hand, but never off the screen
        top = min(max(y - h / 2, 0), sh - h)
        rect = (left, top, left + w, top + h)
        desktop.focus(hwnd)
        desktop.place(hwnd, rect)
        return hwnd, rect

    @staticmethod
    def _jarvis_ui(request, space, props=None):
        """A glass-desk request from JARVIS: a note, a panel, hide / show the panels, put one away."""
        from sensor_os.space import Panel
        kind = request[0]
        if kind == "put_away":
            what = put_away_target(str(request[1]))
            if what == "model" and props is not None:   # the floating 3D models (and any 3D model panel)
                for p in [p for p in props.props if p.control is None]:
                    props.remove(p.id)
            for p in [p for p in space.panels if not p.stashed and (p.kind == what or (
                    what not in KIND_WORDS.values() and what != "model" and what in (p.title or "").lower()))]:
                space._stash(p, [])                # onto the shelf: nothing is lost, grab its tab to bring it back
            return
        if kind == "brain":                        # JARVIS changed its AI: tick it on the AI panels
            space.ai_current = (request[1], request[2])
            for p in space.panels:
                if p.kind == "ai":
                    p.ver += 1
            return
        if kind in ("hide_panels", "show_panels"):
            if kind == "hide_panels":
                space.dismiss()
                if props is not None and props.props:
                    props.clear()                  # "everything away" includes the 3D models
            else:
                space.summon()
                if props is not None:
                    props.restore()
            return
        panel_kind = "note" if kind == "note" else KIND_WORDS.get(str(request[1]).strip().lower(), "note")
        space.hidden = False
        n = len([p for p in space.panels if not p.stashed])
        p = space.add(Panel(space.new_id(), "picker", "", 0.42 + 0.04 * (n % 4), 0.42 + 0.03 * (n % 3), 0.22, 0.26, z=0.05))
        space.fill(p, panel_kind)
        if kind == "note":
            p.data["text"] = str(request[1])
            p.ver += 1

    def _log_hands(self, hands, holo, holo_cmds, space_cmds, now):
        """One line in run.log per click, panel action and lost hand, with where on the screen it was.
        Every frame also goes to trace.csv: where each hand points, how far its fingers are apart, and
        how close it is to the edges of the camera picture."""
        if not hasattr(self, "_trace"):
            self._trace = open(Path(__file__).resolve().parent / "trace.csv", "w", encoding="utf-8", buffering=1)
            self._trace.write("t,hands,slot,x,y,pinch,pinched,fist,blocked,cam_left,cam_right,cam_top,cam_bottom,"
                              "mode,owner,lead,ready,just_pinched\n")
            self._trace_t0 = now
        if not hands:
            self._trace.write(f"{now - self._trace_t0:.3f},0\n")
        for i, s in enumerate(holo.slots):
            if s.active and s.lost_since is None and i < len(hands):
                us = [p[0] for p in hands[i].lm]
                vs = [p[1] for p in hands[i].lm]
                self._trace.write(f"{now - self._trace_t0:.3f},{len(hands)},{i},{s.pos[0]:.3f},{s.pos[1]:.3f},"
                                  f"{(s.pinch or 0):.3f},{int(s.pinched)},{int(s.fist)},{int(s.blocked)},"
                                  f"{min(us):.3f},{max(us):.3f},{min(vs):.3f},{max(vs):.3f},"
                                  f"{holo.mode},{holo.owner},{holo.lead},{int(s.ready(now))},{int(s.just_pinched)}\n")
        seen = [s for s in holo.slots if s.active and s.lost_since is None]
        where = lambda s: f"x={s.pos[0]:.2f} y={s.pos[1]:.2f}"
        if len(hands) < getattr(self, "_hands_before", 0):
            last = getattr(self, "_last_positions", [])
            print(f"[hands] lost a hand; last seen at {', '.join(last) or '?'}", flush=True)
        self._hands_before = len(hands)
        self._last_positions = [where(s) for s in seen]
        cursor = next((c for c in reversed(holo_cmds) if c[0] == "goto"), None)
        for c in holo_cmds:
            if c[0] in ("click", "right_click", "drag_start", "grab_start"):
                at = f"x={cursor[1]:.2f} y={cursor[2]:.2f}" if cursor else ", ".join(self._last_positions)
                print(f"[hands] {c[0]} at {at}", flush=True)
        for c in space_cmds:
            if c[0] not in ("drop",):
                print(f"[hands] panel {c[0]} {c[1] if len(c) > 1 else ''} with hand at "
                      f"{', '.join(self._last_positions) or 'none (thrown)'}",
                      flush=True)
        for s in seen:                                   # a pinch the hand made but nothing took
            if s.just_pinched and not s.blocked and not any(c[0] in ("click",) for c in holo_cmds):
                print(f"[hands] pinch seen at {where(s)}", flush=True)

    @staticmethod
    def _hover_rect(view, sw, sh):
        """Visible frame of the window under the lead hand, for the HUD highlight."""
        for _pts, pos, state, lead in view["hands"]:
            if lead and state != "fist":
                hwnd = desktop.window_at(pos[0] * (sw - 1), pos[1] * (sh - 1))
                return desktop.frame_bounds(hwnd) if hwnd else None
        return None

    @staticmethod
    def _window_command(cmd, dry_run: bool):
        if dry_run:
            print(f"[dry-run] stage {cmd}", flush=True)
            return
        kind = cmd[0]
        try:
            if kind in ("move", "resize"):
                desktop.place(cmd[1], cmd[2])
            elif kind == "maximize":
                desktop.maximize(cmd[1])
            elif kind == "minimize":
                desktop.minimize(cmd[1])
            elif kind == "restore":
                desktop.restore(cmd[1])
            elif kind == "focus":
                desktop.focus(cmd[1])
        except OSError as exc:              # a window that vanished or refuses: keep going
            print(f"Window command {cmd[:2]} failed: {exc}", flush=True)

    def _run(self):
        args, cfg, shared = self.args, self.cfg, self.shared
        controller = DryRunController() if args.dry_run else Controller(desktop.make_input(), cfg.fling_speed)
        desktop.start_hotkeys()
        engine = GestureEngine(cfg)
        self.tracker = HandTracker(cfg.max_hands)
        self.head = HeadTracker(cfg.depth_sensitivity) if cfg.holo_depth and not args.no_hud else None
        self.camera = Camera(cfg.camera_index, cfg.frame_width, cfg.frame_height, cfg.camera_fps)
        enabled = True
        camera_slow = False
        keys_before = {VK_Q: False, VK_H: False, VK_X: False, VK_S: False, VK_M: False, VK_C: False, VK_G: False,
                       VK_J: False, VK_A: False}
        summon = SummonDetector(cfg)
        sw, sh = desktop.screen_size()
        dmap = DesktopMap(sw, sh, cfg.stage_scale)
        stage = None
        last_sync = 0.0
        opened_once = False
        mode = args.mode or cfg.mode
        if mode == "space":
            mode = "holo"              # the glass space and the hologram are one mode now
        holo_on = False
        last_skin = 0.0
        skin = desktop.HoloSkin(cfg.holo_alpha)
        self._scaling = None
        self._grabbed = None
        start = time.perf_counter()
        print("sensor-OS running.  Ctrl+Alt+Q on/off   Ctrl+Alt+H help   Ctrl+Alt+X quit", flush=True)
        shared.update(banner=("SENSOR-OS", tr("Starting up") + "\n" + tr("Camera, hand tracking and glass panels..."), None))

        try:
            self._next()
            shared.update(banner=None)
            cam_aspect = 4 / 3
            if self._frame is not None:
                h, w = self._frame.shape[:2]
                engine.aspect = cam_aspect = w / h
            cmap = CameraMap(cam_aspect, sw / sh, cfg.stage_zoom)
            hmap = CameraMap(cam_aspect, sw / sh, cfg.holo_zoom)
            hub = Hub(cfg, sw / sh, hub_data.provide)
            props = PropSpace(cfg, sw / sh)
            system = self.system = SystemControls(live=not args.dry_run)
            system.start()
            sounds = sound.Player(cfg.sound_volume, cfg.sound)
            dial_step = None
            holo = HoloEngine(cfg, hmap, sw / sh, hub,
                              probe=lambda x, y: desktop.window_at(x * (sw - 1), y * (sh - 1)) is not None,
                              props=props)

            def holo_do(cmds):
                for cmd in cmds:
                    if self._holo_command(cmd, controller, sw, sh, args.dry_run) == "click":
                        shared.bump("click_seq")

            space = SpaceEngine(cfg, hmap, sw / sh, cam_aspect, provide=hub_data.space_provider)
            try:
                space.load(json.loads(SPACE_FILE.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass                                  # first run, or an unreadable file
            os_layout(space, cfg, sw, sh)
            space.hidden = cfg.os_start_hidden             # a clean screen until the panels are called up
            last_refresh = 0.0
            last_space_save = 0.0
            desk = GlassDesk(holo, space)
            jarvis_ui: queue.Queue = queue.Queue()
            jarvis = None
            if cfg.jarvis_enabled and not args.no_hud:
                from sensor_os.jarvis.assistant import Jarvis
                jarvis = self.jarvis = Jarvis(cfg, shared, system, desktop, ui=lambda *a: jarvis_ui.put(a),
                                              chime=sounds.play, folders=hub_data.folder_of,
                                              scene=lambda: describe_scene(space, props))
                jarvis.start()
            space.ai_current = (cfg.jarvis_brain, cfg.jarvis_model)            # one mode: pinch = touch, fist = grab, real windows and panels

            def set_holo(on):
                for ev in engine.release_all():
                    controller.handle(ev)
                controller.release()
                if on:
                    if cfg.holo_demo_prop and not props.props:
                        for ref, x, y in cfg.holo_start_props:
                            props.add(ref, (x, y))
                    if cfg.holo_cardify:
                        skin.cardify(desktop.list_windows())
                    if cfg.holo_hide_icons:
                        skin.hide_icons()
                    if cfg.os_hide_taskbar:
                        skin.hide_taskbar()
                    skin.apply(desktop.list_windows())
                    shared.update(holo=holo.view(), crop=hmap.crop, frame=self._frame, mode="holo")
                else:
                    holo_do(desk.release_all())
                    skin.restore()
                    shared.update(holo=None, frame=None)
                return on

            def cards():
                return [Card(i.hwnd, i.title, dmap.to_stage(i.rect), i.minimized, i.source)
                        for i in desktop.list_windows()[:cfg.stage_max_cards]]

            def open_stage(now):
                for ev in engine.release_all():
                    controller.handle(ev)
                holo_do(holo.release_all())
                controller.release()
                st = StageEngine(cfg, cmap, dmap)
                st.reset(cards())
                shared.update(stage=st.view(), crop=cmap.crop, frame=self._frame, summon=0.0, mode="stage")
                print("Stage open", flush=True)
                return st

            def close_stage():
                summon.armed = False          # hands still up must not reopen it at once
                shared.update(stage=None, frame=None)
                print("Stage closed", flush=True)

            first_time = not cfg.calibrated and not args.skip_calibrate and not args.no_hud and not args.max_seconds
            if args.calibrate or first_time:
                if calibrate.run(self._next_hand, shared, cfg, self.stopped):
                    calibrate.fit(self._next_hand, shared, cfg, self.stopped)
            shared.bump("help_seq")          # show the gesture help once at start

            while not self.stopped():
                hands, now = self._next()
                hand = hands[0] if hands else None
                if args.max_seconds and now - start >= args.max_seconds:
                    print(f"fps: {self._fps:.1f}", flush=True)
                    break

                pressed = {vk: hotkey_down(vk) for vk in keys_before}
                fresh = {vk: pressed[vk] and not keys_before[vk] for vk in pressed}
                keys_before = pressed
                if fresh[VK_X] or STOP_FLAG.exists():      # stop.flag = ask a running copy to shut down cleanly
                    print(f"Quit: {'Ctrl+Alt+X' if fresh[VK_X] else 'stop.flag'} at {time.strftime('%H:%M:%S')}", flush=True)
                    break
                if fresh[VK_H]:
                    shared.bump("help_seq")
                if fresh[VK_J] and jarvis is not None:
                    jarvis.listen_now()                       # talk to JARVIS without saying its name
                if fresh[VK_A] and jarvis is not None:
                    jarvis.next_brain()                       # the next AI: Claude -> Haiku -> Opus -> ChatGPT -> ...
                while not jarvis_ui.empty():                  # what JARVIS asked of the glass desk
                    self._jarvis_ui(jarvis_ui.get_nowait(), space, props)
                if fresh[VK_M]:
                    mode = "wrist" if mode == "holo" else "holo"
                    print("Mode:", mode, flush=True)
                if fresh[VK_G]:
                    out = space.toggle_summon()               # every glass panel up / away at once
                    if holo_on:
                        holo_do(desk.release_all())
                    sounds.play("open" if out else "sweep")
                    print("Glass panels", "shown" if out else "hidden", flush=True)
                if fresh[VK_C] and stage is None:
                    # Hand setup on demand: let go of everything first so nothing is clicked meanwhile.
                    for ev in engine.release_all():
                        controller.handle(ev)
                    holo_do(desk.release_all())
                    controller.release()
                    print("Hand setup", flush=True)
                    if calibrate.run(self._next_hand, shared, cfg, self.stopped):
                        calibrate.fit(self._next_hand, shared, cfg, self.stopped)
                    continue
                if fresh[VK_Q]:
                    enabled = not enabled
                    if not enabled:
                        for ev in engine.release_all():
                            controller.handle(ev)
                        controller.release()
                        if stage is not None:
                            stage = None
                            close_stage()
                    print("Control", "ON" if enabled else "OFF", flush=True)

                if self._frame is not None and now - getattr(self, "_glare_t", -1e9) >= 2.0:
                    self._glare_t = now
                    shared.update(glare=glare_side(self._frame))
                # A camera held by another program delivers ~1 black frame a second.
                slow = now - start > 4 and 0 < self._fps < 8
                if slow != camera_slow:
                    camera_slow = slow
                    shared.update(banner=(tr("Camera is very slow"), tr("Another program may be using the camera.\nClose it, then start sensor-OS again."), None)
                                  if slow else None)
                    if slow:
                        print("Warning: camera is very slow. Is another program using it?", flush=True)

                want_holo = enabled and mode == "holo"
                if want_holo != holo_on:
                    holo_on = set_holo(want_holo)

                # ---- stage mode
                want = fresh[VK_S] or (args.stage and not opened_once)
                if stage is None and enabled and (want or (cfg.summon_gesture and not holo_on and summon.update(hands, now))):
                    opened_once = True
                    stage, last_sync = open_stage(now), now
                    continue
                if stage is not None:
                    if fresh[VK_S]:
                        stage = None
                        close_stage()
                        continue
                    if now - last_sync >= 0.5:
                        stage.sync(cards(), now)
                        last_sync = now
                    for cmd in stage.update(hands, now):
                        self._window_command(cmd, args.dry_run)
                    if stage.done:
                        stage = None
                        close_stage()
                        continue
                    shared.update(stage=stage.view(), frame=self._frame, lm=hand.lm if hand else None,
                                  mode="stage", enabled=enabled)
                    continue

                # ---- hologram mode: both hands act directly on the real desktop
                if holo_on:
                    mode_before = holo.mode
                    typed = shared.take_keys()
                    if typed:
                        space.type_text(typed)
                    holo_cmds, space_cmds = desk.update(hands, now)
                    self._log_hands(hands, holo, holo_cmds, space_cmds, now)
                    if any(cmd[0] in ("clear", "unclear") for cmd in holo_cmds):
                        out = space.toggle_summon()      # a clap calls every glass panel up / sends them away
                        holo_cmds = [cmd for cmd in holo_cmds if cmd[0] not in ("clear", "unclear")]   # windows stay
                        sounds.play("open" if out else "sweep")
                        print("Clap: glass panels", "shown" if out else "hidden", flush=True)
                    holo_do(holo_cmds)
                    sounds.play(sound.which(mode_before, holo.mode, holo_cmds, cfg.fling_speed))
                    if now - last_refresh >= 1.0:
                        space.refresh()                  # open windows and apps change behind our back
                        last_refresh = now
                    quit_now = False
                    for kind, arg in space_cmds:
                        if kind == "focus":
                            sounds.play("open")
                            if not args.dry_run:
                                desktop.focus(arg)
                        elif kind == "armed":
                            sounds.play("right")
                        elif kind == "power":
                            print("Power:", arg, flush=True)
                            sounds.play("sweep")
                            if arg == "exit":
                                quit_now = True
                            elif not args.dry_run:
                                desktop.power(arg)
                        elif kind in ("grab", "drop", "throw"):
                            sounds.play(kind)
                        elif kind == "press":
                            sounds.play("touch")
                        elif kind in ("create", "make", "next", "edit"):
                            sounds.play("open" if kind == "create" else "click")
                        elif kind == "delete":
                            sounds.play("sweep")
                        elif kind == "media":
                            system.request("media", arg)
                            sounds.play("click")
                        elif kind == "ai":
                            sounds.play("click")
                            if jarvis is not None:
                                jarvis.set_brain(*arg, announce=True)   # it says which AI it is now
                            else:
                                space.ai_current = arg
                        elif kind == "open_file":
                            sounds.play("open")
                            if arg and not args.dry_run:
                                try:
                                    hub_data.open_path(arg)
                                except OSError as exc:
                                    print(f"Could not open {arg}: {exc}", flush=True)
                    if space.dirty and now - last_space_save >= 2.0:
                        try:
                            SPACE_FILE.write_text(json.dumps(space.dump(), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
                            space.dirty = False
                        except OSError as exc:
                            print(f"Could not save the glass panels: {exc}", flush=True)
                        last_space_save = now
                    for name, action in props.drain():      # dials and buttons on the floating controls
                        system.request(name, action)
                        if isinstance(action, str):
                            sounds.play("click")
                        elif round(action * 20) != dial_step:   # one tick per 5%
                            dial_step = round(action * 20)
                            sounds.play("tick")
                    props.sync(system.state)
                    if now - last_skin >= 1.0:
                        skin.apply(desktop.list_windows())
                        last_skin = now
                    view = holo.view()
                    view["target"] = self._hover_rect(view, sw, sh)
                    panels = space.view()
                    panels["sys"] = dict(system.state)
                    panels["parallax"] = cfg.space_parallax
                    panels["statusbar"] = cfg.os_statusbar
                    view["panels"] = panels
                    view["room"] = cfg.holo_room
                    if self.head is not None and self._frame is not None:
                        view["head"] = self.head.update(self._frame, now)
                        view["depth"] = (cfg.depth_turn, cfg.depth_shift)
                    shared.update(holo=view, frame=self._frame, crop=hmap.crop, mode="holo",
                                  lm=hand.lm if hand else None, enabled=enabled, summon=0.0)
                    if quit_now:
                        print(f"Quit: the power panel's exit tile at {time.strftime('%H:%M:%S')}", flush=True)
                        break
                    if args.debug and self._frame is not None:
                        preview.draw(self._frame, hand.lm if hand else None, "holo " + holo.mode, 0.0,
                                     self._fps, enabled, args.dry_run)
                        if preview.poll_key() == 27:
                            break
                    continue

                shared.update(summon=summon.progress)
                events = engine.update(hand, now, hands[1] if len(hands) > 1 else None)

                if enabled:
                    for ev in events:
                        if controller.handle(ev) == "fling" or ev.kind == "click":
                            shared.bump("click_seq")
                        elif ev.kind == "right_click":
                            shared.bump("rclick_seq")

                shared.update(mode=engine.mode, progress=engine.progress, hold=engine.hold,
                              scroll_axis=engine.scroll_axis, lm=hand.lm if hand else None, enabled=enabled)

                if args.debug and self._frame is not None:
                    preview.draw(self._frame, hand.lm if hand else None, engine.mode, engine.progress,
                                 self._fps, enabled, args.dry_run)
                    if preview.poll_key() == 27:  # Esc
                        break
                if args.max_seconds and now - start >= args.max_seconds:
                    print(f"fps: {self._fps:.1f}", flush=True)
                    break
        finally:
            if getattr(self, "jarvis", None) is not None:
                self.jarvis.stop()
            for ev in engine.release_all():
                controller.handle(ev)
            skin.restore()
            shared.update(holo=None, stage=None)
            controller.close()
            self.camera.close()
            self.tracker.close()
            if self.head is not None:
                self.head.close()
            if args.debug:
                preview.close()


def glare_side(frame):
    """Which third of the (mirrored) camera picture is blown out by a bright window or lamp, if any.
    A hand in front of glare loses its outline and the hand model drops it."""
    small = cv2.cvtColor(cv2.resize(frame, (96, 72)), cv2.COLOR_BGR2GRAY)
    thirds = {"left": small[:, :32], "middle": small[:, 32:64], "right": small[:, 64:]}
    worst = max(thirds, key=lambda k: float((thirds[k] > 245).mean()))
    return worst if float((thirds[worst] > 245).mean()) > 0.12 else None


def os_layout(space, cfg, sw, sh) -> None:
    """The first time: a clock and the machine's state on top, apps on the left, open windows and
    power on the right. Once, so a panel you threw away does not come back."""
    from sensor_os.space import Panel
    if not space.panels:
        space.panels = initial_panels([], (sw, sh), cfg)
    if not cfg.os_ai_panel:                      # once: the panel that picks JARVIS's AI, where it is seen
        if cfg.jarvis_enabled and not any(p.kind == "ai" for p in space.panels):
            space.fill(space.add(Panel(space.new_id(), "ai", "", 0.50, 0.70, 0.30, 0.17, z=0.05)), "ai")
        cfg.os_ai_panel = True
        cfg.save(("os_ai_panel",))
    if cfg.os_layout:
        return
    have = {p.kind for p in space.panels}
    for kind, x, y, w, h in (("apps", 0.17, 0.53, 0.26, 0.42), ("windows", 0.84, 0.47, 0.24, 0.30),
                             ("power", 0.88, 0.75, 0.16, 0.16)):
        if kind not in have:
            space.fill(space.add(Panel(space.new_id(), kind, "", x, y, w, h, z=0.1)), kind)
    cfg.os_layout = True
    cfg.save(("os_layout",))


def main():
    if sys.stdout is None:                       # started without a console (pythonw, at login): log to a file
        log = open(Path(__file__).resolve().parent / "run.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    for stream in (sys.stdout, sys.stderr):      # Thai file names must never crash a print
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args()
    if args.autostart:
        from sensor_os import autostart
        if args.autostart == "on":
            print("sensor-OS will start at login:", autostart.enable())
        else:
            print("Start at login removed." if autostart.disable() else "It was not set to start at login.")
        return
    cfg = Config.load()
    cfg.camera_index = args.camera
    shared = Shared()
    shared.update(dry_run=args.dry_run)

    app = None
    if args.no_hud:
        make_dpi_aware()
    else:
        from sensor_os import hud
        app = hud.create_app()

    STOP_FLAG.unlink(missing_ok=True)
    pipeline = Pipeline(args, cfg, shared)
    pipeline.start()
    try:
        if app is not None:
            hud.run(app, shared)
        else:
            while pipeline.is_alive():
                pipeline.join(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        pipeline.join(5)

    error = shared.get().error
    if error:
        print(f"Stopped with an error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
