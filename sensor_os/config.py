"""All tunable values in one place. Per-user overrides live in settings.json."""
import json
from dataclasses import dataclass
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"


@dataclass
class Config:
    language: str = "en"          # on-screen text: "en" or "th" (read from settings.json at start)

    # ---- Camera
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    camera_fps: int = 60          # requested; the camera falls back to what it supports
    max_hands: int = 2            # the second hand is only used for two-fist zoom

    # ---- Cursor: relative "air trackpad", tuned for wrist-only movement with the forearm resting
    # Hand motion is measured in hand-lengths (wrist -> middle knuckle), so the
    # feel is the same near or far from the camera. Output is in screen heights.
    gain_slow: float = 0.60       # screen heights per hand-length when moving slowly (precision)
    gain_fast: float = 5.5        # ...when moving fast (crossing the screen)
    speed_lo: float = 0.25        # hand-lengths/second where acceleration starts
    speed_hi: float = 3.0         # ...and where it tops out
    move_deadzone: float = 0.04   # hand-lengths/second below which the cursor holds still
    max_speed: float = 18.0       # faster than this is a tracking glitch, ignored
    cursor_speed: float = 1.0     # overall multiplier
    filter_min_cutoff: float = 1.0   # lower = steadier pointer when the hand moves slowly (aiming)
    filter_beta: float = 8.0
    engage_delay: float = 0.15    # seconds after a hand appears before it controls anything
    lost_grace: float = 0.25      # seconds without a hand before buttons are released

    # ---- Pinch (3D distance thumb tip <-> fingertip, relative to hand size)
    pinch_on_index: float = 0.38  # generous on purpose: a click should not need the fingers pressed hard together
    pinch_off_index: float = 0.56
    pinch_tau: float = 0.03       # seconds of smoothing on the pinch distance (frame-rate independent)
    pinch_reach_min: float = 0.90 # index tip at least this far from the wrist: a fully folded finger cannot pinch
    pinch_open_floor: float = 0.35 # the relaxed-hand level never goes below this
    pinch_open_decay: float = 0.04 # how fast it forgets a wide-open hand (hand lengths per second)
    pinch_arm_time: float = 0.35  # a pinch counts only if thumb and index were clearly apart this recently
    pointer_anchor: str = "knuckles"  # where the pointer sits: "knuckles" (steady through a pinch) or "pinch"
    progress_open: float = 3.0    # distance (x threshold) at which the HUD ring starts closing
    calibrated: bool = False

    # ---- Click / drag
    drag_move: float = 0.035      # screen heights moved while pinched before it becomes a drag
    right_hold: float = 2.00      # seconds a pinch is held still before it right-clicks
    right_hold_show: float = 0.25 # seconds before the HUD starts showing the hold ring
    click_sticky: float = 0.40    # seconds the cursor resists moving after a click (for double-click)
    sticky_move: float = 0.02     # screen heights that break the stickiness

    # ---- Scroll (hold up three fingers - index, middle, ring - and swipe: the page follows)
    scroll_start: float = 0.06    # hand-lengths moved before scrolling starts
    three_ext: float = 1.45       # index/middle/ring tips at least this far from the wrist
    three_pinky: float = 1.25     # ...while the little finger is folded closer than this
    three_pinky_exit: float = 1.40
    three_hold: float = 0.08      # seconds the shape must hold
    scroll_gain: float = 2600.0   # wheel units per hand-length (120 = one notch)
    scroll_step: int = 30
    scroll_invert: bool = False

    # ---- Fling a window: drag its title bar and release while still moving fast
    fling_speed: float = 4.5      # screen heights/second at release
    fling_window: float = 0.15    # seconds of motion the release speed is measured over

    # ---- Two-fist zoom: make two fists, pull apart / push together
    zoom_gain: float = 300.0      # wheel units per hand-length of change (120 = one zoom step)
    zoom_step: int = 120
    zoom_deadzone: float = 0.03   # hand-lengths

    # ---- Hologram mode (both hands act directly on the real desktop)
    mode: str = "holo"            # "holo" = the glass desk (real windows + glass panels); "wrist" = air trackpad
    holo_zoom: float = 0.66       # part of the camera image used. The screen edges map to 17% in from the
                                  # camera's edges, so a hand pointing there stays fully in view (at 0.80 the
                                  # thumb or little finger left the picture and pinches failed at the sides)
    holo_alpha: int = 208         # window opacity 0..255 while the hologram is on (255 = leave windows alone)
    holo_tint: int = 28           # how much the camera wallpaper is darkened, 0..255 (0 = as the camera sees it)
    holo_camera: bool = False     # show the mirrored camera image as the wallpaper (False = your normal desktop)
    holo_skeleton: bool = False   # draw the whole glowing hand skeleton; False = just a ring on the pinch point
    holo_hide_icons: bool = True  # hide the desktop icons while the glass desk is on (back when it closes; files: Files panel)
    # ---- Glass space (mode "space"): grab glass panels, nothing else to learn
    space_shrink: float = 0.55    # how much smaller a panel looks at the very back (z = 1)
    space_depth_gain: float = 1.1 # depth change per log of palm-size change (hand to / from the camera)
    space_depth_dead: float = 0.10  # palm-size changes smaller than ~10% do not move the panel in depth
    space_press_move: float = 0.06 # a pinch on a panel still presses if the hand drifts less than this
    space_tile_magnet: float = 0.03  # a touch this close to a button on a panel presses that button
    space_magnet: float = 0.05    # a grab this close to a panel (screen heights) still catches it
    space_pair_time: float = 0.45 # both hands must close on empty space within this to draw a new panel
    space_min_new: float = 0.08   # smallest new panel drawn with two hands (screen heights)
    space_lead: float = 0.05      # seconds the hand point is pushed ahead to hide the camera's delay
    space_friction: float = 4.0   # how fast a let-go panel slows down (1/s)
    space_bounce: float = 0.35    # speed kept when a panel bounces off the screen edge
    space_fling: float = 3.2     # screen heights/s at release that throws a panel onto the shelf
    space_tap_time: float = 0.35  # grab + let go quicker than this, without moving = open it
    space_tap_move: float = 0.03  # screen fractions the hand may move and still be a tap
    space_min_h: float = 0.10     # smallest / largest panel height when stretched with two hands
    space_max_h: float = 0.95
    space_max_windows: int = 8
    space_parallax: float = 0.035 # how far near panels shift as the head moves (far ones shift less)
    # ---- JARVIS: "Hey Jarvis", then talk
    jarvis_enabled: bool = True
    jarvis_brain: str = "claude_code"   # the AI: "claude_code" (Claude plan), "chatgpt" (ChatGPT plan, Codex CLI),
                                        # "gemini" (Gemini CLI + GEMINI_API_KEY)
    jarvis_claude_path: str = ""        # where claude.exe / codex / gemini are, if not found by themselves
    jarvis_codex_path: str = ""
    jarvis_gemini_path: str = ""
    jarvis_model: str = ""              # "" = that AI's default. Claude: "haiku" / "sonnet" / "opus";
                                        # ChatGPT: e.g. "gpt-5.6-terra"; Gemini: e.g. "gemini-2.5-flash"
    jarvis_voice: str = "en-GB-RyanNeural"    # any Microsoft neural voice (`edge-tts --list-voices`),
                                              # e.g. th-TH-NiwatNeural for Thai, de-DE-ConradNeural for German
    jarvis_language: str = "en-US"      # what you speak (speech recognition + JARVIS's replies), e.g. th-TH
    jarvis_languages: tuple = ()        # several, main first, e.g. ["en-US", "th-TH"]: JARVIS hears whichever
                                        # you speak (recognized side by side, no slower) and answers in it
    jarvis_pause: float = 0.6           # seconds of quiet that end what you say (shorter = answers sooner,
                                        # too short cuts you off mid-sentence)
    jarvis_quick: bool = True           # do plain commands ("open Chrome", "volume up") at once, without the AI
    jarvis_wake_threshold: float = 0.3  # how sure the wake-word model must be (higher = fewer false wakes);
                                        # a Thai-accented "Hey Jarvis" scored 0.38-0.45 in tests, so 0.5 missed it
    jarvis_name_check: bool = True      # also wake on the name alone ("Jarvis"): a short phrase said on its
                                        # own is checked by Google's speech recognition
    jarvis_followup: float = 6.0        # seconds JARVIS keeps listening after answering, without its name
    sound: bool = True           # short sounds when a gesture lands (pinch seen, click, grab, throw...)
    sound_volume: float = 0.30    # 0..1
    claw_enabled: bool = True     # claw (fingers spread and half curled) = pull the next window to the hand
    claw_min: float = 1.10        # every fingertip farther from the wrist than this (else it is a fist)...
    claw_max: float = 1.58        # ...and nearer than this (else it is an open hand)
    claw_spread: float = 0.85     # index-to-little fingertip distance, in hand lengths: a relaxed hand is narrower
    claw_open: float = 1.62       # index, middle and ring fingertips beyond this = the hand opened: let go
    claw_hold: float = 0.35       # seconds the claw must be held before it pulls
    claw_cooldown: float = 0.8    # seconds after letting go before another pull
    os_hide_taskbar: bool = True  # sensor-OS takes the whole screen: the Windows taskbar hides (back on quit)
    os_statusbar: bool = True     # time, Wi-Fi, volume and battery along the top
    os_layout: bool = False       # set once the apps / windows / power panels have been put out
    os_ai_panel: bool = False     # set once the "JARVIS AI" panel has been put on the desk
    os_start_hidden: bool = True  # start with a clean screen; clap / Ctrl+Alt+G / "show panels" calls every panel up
    jarvis_greeting: bool = True  # JARVIS says hello when sensor-OS starts
    jarvis_tools: tuple = ()      # extra services JARVIS may use, by name from `claude mcp list` (e.g. "Notion",
                                  # "Canva"); your account's own connectors (Gmail, Drive, Shopify) are always on.
                                  # Every one added costs time and tokens on each task: all of them do not fit
    jarvis_tools_model: str = "haiku"    # the Claude model that does the work (haiku = fastest)
    jarvis_tools_timeout: float = 150.0  # seconds before a task is given up
    jarvis_live: bool = True     # keep ChatGPT open between questions (~2 s a reply instead of ~9 s)
    holo_room: bool = True        # the desktop wallpaper becomes the glass room while the hologram is on
    holo_demo_prop: bool = False # float the starting controls on the desktop at start
    holo_start_props: tuple = (("control:volume", 0.74, 0.22), ("control:brightness", 0.90, 0.22),
                               ("control:system", 0.90, 0.58))
    control_size: float = 0.24    # diameter of a control (volume dial, system orb...), in screen heights
    control_gain: float = 2.4     # dial level change per screen height of hand travel (0.42 of travel = 0 to 100%)
    control_swipe: float = 0.10   # sideways travel on the media control that means next / previous
    holo_depth: bool = True       # track the head so floating things show depth as you move
    depth_turn: float = 0.55      # radians a 3D model turns when the head is at the edge of the camera view
    depth_shift: float = 0.09     # how far it slides the other way, as a fraction of its size
    depth_sensitivity: float = 1.6
    prop_size: float = 0.42       # diameter of a new 3D model, in screen heights
    prop_rot_gain: float = 5.0    # radians of turn per screen height of hand travel
    prop_spin: float = 0.5        # idle spin, radians per second
    holo_cardify: bool = True     # shrink maximized windows into floating cards (maximized again on exit)
    hub_x: float = 0.10           # where the ring menu starts
    hub_y: float = 0.30
    clap_enabled: bool = True     # clap = put every window away
    clap_near: float = 0.30       # hands this close (screen heights)...
    clap_far: float = 0.62        # ...after being this far apart...
    clap_window: float = 0.5      # ...within this many seconds
    holo_rewind: float = 0.06     # seconds to look back for where the hand aimed (the knuckles barely move)
    holo_drag_move: float = 0.045 # screen fraction a pinch moves before it becomes a drag (smaller = taps turn into drags)
    holo_click_delay: float = 0.12 # the click happens this long after the fingers meet, not when they open again
                                  # (measured: people hold a pinch ~1 s, and the hand drifts meanwhile); the short
                                  # wait lets a pinch that is really the start of a fist (a grab) click nothing.
                                  # None = click on letting go, the old way
    holo_click_still: float = 0.30 # ...if the hand is this still (screen heights/s); measured drift ~0.1
    holo_sticky: float = 0.035    # screen fraction the hand may wander and still double-click the same spot
    holo_double_time: float = 0.9 # seconds between two taps that still count as a double click
    holo_scroll_start: float = 0.02
    holo_scroll_gain: float = 5000.0  # wheel units per screen height of hand travel
    holo_min_w: int = 320         # smallest window size when resizing with two hands
    holo_min_h: int = 220

    # ---- Stage mode (real windows floating over the mirrored camera image)
    summon_gesture: bool = True   # raise both hands open to open the stage
    summon_hold: float = 0.7      # seconds both hands stay open
    open_reach: float = 1.55      # every fingertip at least this far from the wrist = open hand
    stage_zoom: float = 0.80      # part of the camera image shown (smaller = less reaching)
    stage_scale: float = 0.80     # size of the desktop map on the stage
    stage_tap_time: float = 0.40  # a pinch shorter than this, without moving, is a tap
    stage_tap_move: float = 0.025 # stage units the pinch may move and still be a tap
    stage_fling: float = 2.2      # stage widths/second at release that throws a card away
    stage_max_frac: float = 0.92  # resized to this share of the desktop map = maximize
    stage_min_size: float = 0.10  # smallest card width while resizing
    stage_exit_hold: float = 0.35 # seconds two fists must hold to leave the stage
    stage_max_cards: int = 14

    # ---- Fist detection
    fist_enter: float = 0.95      # all fingertips closer to the wrist than this
    fist_exit: float = 1.15
    fist_hold: float = 0.10       # seconds
    resume_delay: float = 0.25    # seconds the cursor waits after the fist opens

    @property
    def aspect(self) -> float:
        return self.frame_width / self.frame_height

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "Config":
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for key, value in data.items():
                if hasattr(cfg, key) and not key.startswith("_"):
                    setattr(cfg, key, type(getattr(cfg, key))(value))
        return cfg

    def save(self, keys, path: Path = SETTINGS_PATH) -> None:
        data = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
        for key in keys:
            data[key] = getattr(self, key)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
