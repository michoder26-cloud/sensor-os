"""Short setup that fits the pinch thresholds to the user's own hand.

Step 1 measures how far apart thumb and index are with the hand open.
Step 2 waits until it actually sees the fingers come together, then measures the pinch.
Waiting for the pinch (instead of measuring on a timer) is what makes it pass: nothing is
recorded while the user is still reading the instruction.
"""
import statistics

from .gestures import pinch_features
from .i18n import tr

PREP = 2.0          # seconds to read the first instruction
MEASURE_OPEN = 1.5  # seconds of open-hand samples
MEASURE_PINCH = 1.2 # seconds the pinch must be held (they add up; letting go only pauses it)
WAIT = 25.0         # seconds before a step gives up
CLOSE_RATIO = 0.72  # "pinched" during setup = closer than this share of the open distance
KEYS = ("pinch_on_index", "pinch_off_index", "calibrated")


def thresholds(open_d: float, closed_d: float):
    """(on, off) between the pinched and the open distance. Always usable: a click must never be impossible."""
    span = max(open_d - closed_d, 0.0)
    on = min(max(closed_d + 0.45 * span, closed_d + 0.08), 0.60)
    off = min(max(closed_d + 0.70 * span, on + 0.10), 0.85)
    return round(on, 3), round(off, 3)


def run(next_hand, shared, cfg, should_stop) -> bool:
    """next_hand() -> (Hand | None, time). Returns True if new thresholds were saved."""
    # ---- step 1: open hand
    title = tr("Hand setup  {} / {}").format(1, 2)
    text = tr("Hold your hand open and relaxed in front of the camera")
    samples, started, deadline = [], None, None
    while True:
        if should_stop():
            shared.update(banner=None)
            return False
        hand, t = next_hand()
        deadline = deadline or t + WAIT
        if hand is None:
            if t > deadline:
                return _fail(next_hand, shared, should_stop, tr("No hand seen by the camera"), "no hand in step 1")
            shared.update(banner=(title, text + tr("\n(raise your hand into view)"), 0.0))
            continue
        started = started or t
        elapsed = t - started
        if elapsed < PREP:
            shared.update(banner=(title, text, 0.0))
        elif elapsed < PREP + MEASURE_OPEN:
            samples.append(pinch_features(hand.world)[0])
            shared.update(banner=(title, text + tr("\nHold still..."), (elapsed - PREP) / MEASURE_OPEN))
        else:
            break
    if len(samples) < 5:
        return _fail(next_hand, shared, should_stop, tr("Not enough samples"), f"only {len(samples)} open samples")
    open_d = statistics.median(samples)
    gate = max(open_d * CLOSE_RATIO, 0.20)      # a hand whose thumb already rests near the index still gets a gate

    # ---- step 2: pinch, measured only while the fingers really are together
    title = tr("Hand setup  {} / {}").format(2, 2)
    text = tr("Pinch thumb and index finger together and hold")
    samples, held, last, deadline, lowest = [], 0.0, None, None, open_d
    while held < MEASURE_PINCH:
        if should_stop():
            shared.update(banner=None)
            return False
        hand, t = next_hand()
        deadline = deadline or t + WAIT
        if t > deadline:
            if len(samples) >= 4:
                break                           # short of time but we did see a pinch: use it
            return _fail(next_hand, shared, should_stop, tr("No pinch was seen"),
                         f"no pinch: open {open_d:.2f}, lowest seen {lowest:.2f}, gate {gate:.2f}")
        dt, last = (t - last if last is not None else 0.0), t
        p = pinch_features(hand.world)[0] if hand is not None else None
        if p is not None:
            lowest = min(lowest, p)
        if p is not None and p < gate:
            samples.append(p)
            held += min(dt, 0.2)
            shared.update(banner=(title, text + tr("\nHold still..."), held / MEASURE_PINCH))
        else:
            hint = tr("\n(raise your hand into view)") if hand is None else tr("\n(touch your thumb tip to your index fingertip)")
            shared.update(banner=(title, text + hint, held / MEASURE_PINCH))

    closed_d = statistics.quantiles(samples, n=4)[0] if len(samples) >= 4 else min(samples)
    on, off = thresholds(open_d, closed_d)
    cfg.pinch_on_index, cfg.pinch_off_index = on, off
    cfg.calibrated = True
    cfg.save(KEYS)
    print(f"Calibrated: open {open_d:.2f}, pinched {closed_d:.2f} -> pinch on {on}, off {off}", flush=True)
    _hold(next_hand, shared, should_stop, (tr("Hand setup done"), tr("Ready"), 1.0), 1.5)
    return True


def _fail(next_hand, shared, should_stop, reason: str, detail: str = "") -> bool:
    print(f"Calibration failed ({detail}): keeping the previous thresholds", flush=True)
    _hold(next_hand, shared, should_stop,
          (tr("Hand setup failed"), tr("{}\nUsing the previous values. Press Ctrl+Alt+C to try again.").format(reason), None), 3.5)
    return False


def _hold(next_hand, shared, should_stop, banner, seconds: float) -> None:
    shared.update(banner=banner)
    _, t0 = next_hand()
    while not should_stop():
        _, t = next_hand()
        if t - t0 >= seconds:
            break
    shared.update(banner=None)


# ---------------------------------------------------------------- fitting every gesture to this hand
# The pinch setup above fits one number. The other shapes (fist, three fingers, claw) were tuned on a
# model hand; real hands differ a lot in finger length and how far they curl. fit() asks for each
# shape once, measures it, and puts every threshold halfway between the shapes it has to tell apart.
import json
import math
from pathlib import Path

PROFILE_PATH = Path(__file__).resolve().parent.parent / "hand_profile.json"
READ = 2.6          # seconds to read each instruction and get the hand into shape
MEASURE = 1.4       # seconds measured
FIT_KEYS = ("fist_enter", "fist_exit", "three_ext", "three_pinky", "three_pinky_exit",
            "claw_min", "claw_max", "claw_open", "claw_spread")
SHAPES = (
    ("open", "Open hand, fingers straight"),
    ("fist", "Fist"),
    ("three", "3 fingers up: index, middle, ring (little finger folded)"),
    ("claw", "Claw: spread your fingers and half curl them, as if grabbing a ball"),
)


def measure(hand) -> list:
    """[pinch, reach index, middle, ring, little, spread] for one frame."""
    w = hand.world
    pinch, reach = pinch_features(w)
    size = math.dist(w[0], w[9]) or 1e-9
    return [pinch, *reach, math.dist(w[8], w[20]) / size]


def _between(a: float, b: float, share: float) -> float:
    return round(a + (b - a) * share, 3)


def derive(shapes: dict) -> tuple:
    """shapes: name -> median [pinch, reach x4, spread]. Returns (new settings, notes on what was skipped)."""
    out, notes = {}, []
    o, f, th, c = (shapes.get(k) for k in ("open", "fist", "three", "claw"))
    if o and f:
        fist_far, open_near = max(f[1:5]), min(o[1:5])
        if open_near - fist_far > 0.25:
            out["fist_enter"] = _between(fist_far, open_near, 0.40)
            out["fist_exit"] = _between(fist_far, open_near, 0.62)
        else:
            notes.append(f"fist looks like the open hand (fist {fist_far:.2f}, open {open_near:.2f})")
    if o and th:
        fist_exit = out.get("fist_exit", 1.15)
        ext, pinky, pinky_open = min(th[1:4]), th[4], o[4]
        if pinky_open - pinky > 0.15 and ext > pinky + 0.15:
            out["three_ext"] = round(max(ext * 0.86, fist_exit + 0.05), 3)
            out["three_pinky"] = _between(pinky, min(pinky_open, ext), 0.50)
            out["three_pinky_exit"] = _between(pinky, min(pinky_open, ext), 0.75)
        else:
            notes.append(f"little finger not clearly folded for three fingers (folded {pinky:.2f}, open {pinky_open:.2f})")
    if o and c:
        fist_far = max(f[1:5]) if f else 0.9
        near, far, spread = min(c[1:5]), max(c[1:5]), c[5]
        open_far, open_mid = max(o[1:5]), min(o[1:4])
        if open_far - far > 0.10 and near - fist_far > 0.10:
            out["claw_min"] = _between(fist_far, near, 0.50)
            out["claw_max"] = _between(far, open_far, 0.50)
            out["claw_open"] = _between(max(c[1:4]), open_mid, 0.60)
            out["claw_spread"] = round(spread * 0.80, 3)
        else:
            notes.append(f"claw not clearly between fist and open hand (claw {near:.2f}-{far:.2f}, "
                         f"fist {fist_far:.2f}, open {open_far:.2f})")
    return out, notes


def fit(next_hand, shared, cfg, should_stop, path: Path = PROFILE_PATH) -> bool:
    """Ask for each hand shape, measure it, fit the thresholds. Returns True if anything was saved."""
    shapes, quality = {}, {}
    for n, (name, prompt) in enumerate(SHAPES, 1):
        title = tr("Hand shapes  {} / {}").format(n, len(SHAPES))
        text = tr(prompt)
        rows, frames, seen, t0, first = [], 0, 0, None, None
        while True:
            if should_stop():
                shared.update(banner=None)
                return False
            hand, t = next_hand()
            t0 = t0 if t0 is not None else t
            elapsed = t - t0
            if elapsed >= READ + MEASURE:
                break
            if elapsed < READ:
                shared.update(banner=(title, text, 0.0))
                continue
            first = first if first is not None else t
            frames += 1
            if hand is None:
                shared.update(banner=(title, text + tr("\n(raise your hand into view)"), (elapsed - READ) / MEASURE))
                continue
            seen += 1
            rows.append(measure(hand))
            shared.update(banner=(title, text + tr("\nHold still..."), (elapsed - READ) / MEASURE))
        quality[name] = {"frames": frames, "seen": seen,
                         "fps": round(frames / max(t - first, 1e-6), 1) if first is not None else 0.0}
        if len(rows) >= 5:
            shapes[name] = [round(statistics.median(col), 3) for col in zip(*rows)]

    new, notes = derive(shapes)
    for key, value in new.items():
        setattr(cfg, key, value)
    if new:
        cfg.save(tuple(new))
    try:
        path.write_text(json.dumps({"columns": ["pinch", "index", "middle", "ring", "little", "spread"],
                                    "shapes": shapes, "quality": quality, "fitted": new, "skipped": notes},
                                   indent=2), encoding="utf-8")
    except OSError:
        pass
    print(f"Hand fitted: {new}" + (f"  skipped: {notes}" if notes else ""), flush=True)
    done = (tr("Hand setup done"), tr("Gestures now fit your hand"), 1.0) if new and not notes else \
           (tr("Hand setup done"), tr("Some shapes were unclear: kept the old values for those"), None)
    _hold(next_hand, shared, should_stop, done, 2.0)
    return bool(new)
