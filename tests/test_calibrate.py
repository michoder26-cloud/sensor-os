"""Hand setup, driven by a scripted hand instead of a camera."""
from sensor_os import calibrate
from sensor_os.config import Config
from sensor_os.shared import Shared
from tests.test_gestures import OPEN, hand

DT = 1 / 17          # a dim room: the webcam's worst case


def pinched(gap):
    return hand(fingers=("pinch", "open", "open", "open"), thumb=True, gap=gap)


def run(script, tmp_path):
    """script: list of (seconds, hand-or-None). The last entry repeats forever."""
    frames = [h for seconds, h in script for _ in range(max(1, int(seconds / DT)))]
    state = {"i": 0, "t": 0.0}

    def next_hand():
        state["t"] += DT
        h = frames[min(state["i"], len(frames) - 1)]
        state["i"] += 1
        return h, state["t"]

    cfg, shared = Config(), Shared()
    cfg.save = lambda keys, path=None: None          # never touch the real settings.json
    ok = calibrate.run(next_hand, shared, cfg, lambda: False)
    return ok, cfg, shared, state["t"]


def test_passes_when_the_user_pinches_late(tmp_path):
    # Still reading for 6 s with an open hand before pinching: the old timer-based setup failed here.
    ok, cfg, shared, _ = run([(4, hand(fingers=OPEN)), (6, hand(fingers=OPEN)), (5, pinched(0.08))], tmp_path)
    assert ok and cfg.calibrated
    assert 0.1 < cfg.pinch_on_index < cfg.pinch_off_index
    assert shared.get().banner is None


def test_pinch_held_in_pieces_still_counts(tmp_path):
    script = [(4, hand(fingers=OPEN))] + [(0.5, pinched(0.08)), (0.5, hand(fingers=OPEN))] * 6
    ok, cfg, _, _ = run(script, tmp_path)
    assert ok


def test_loose_pinch_gives_a_generous_threshold(tmp_path):
    ok, cfg, _, _ = run([(4, hand(fingers=OPEN)), (5, pinched(0.45))], tmp_path)    # fingers not quite touching
    assert ok and cfg.pinch_on_index > 0.45                                           # that pinch must click


def test_never_pinching_fails_and_keeps_the_old_values(tmp_path):
    before = Config().pinch_on_index
    ok, cfg, _, elapsed = run([(40, hand(fingers=OPEN))], tmp_path)
    assert not ok and cfg.pinch_on_index == before and not cfg.calibrated
    assert elapsed < 40


def test_no_hand_at_all_fails(tmp_path):
    ok, cfg, _, _ = run([(40, None)], tmp_path)
    assert not ok


def test_thresholds_are_always_usable():
    for open_d, closed_d in ((1.4, 0.1), (0.5, 0.3), (0.3, 0.28), (0.2, 0.25)):
        on, off = calibrate.thresholds(open_d, closed_d)
        assert closed_d < on < off <= 0.85


# ---------------------------------------------------------------- fitting the other shapes
from tests.test_gestures import FIST, THREE
from tests.test_holo import claw_at


def fit(script, tmp_path):
    frames = [h for seconds, h in script for _ in range(max(1, int(seconds / DT)))]
    state = {"i": 0, "t": 0.0}

    def next_hand():
        state["t"] += DT
        h = frames[min(state["i"], len(frames) - 1)]
        state["i"] += 1
        return h, state["t"]

    cfg, shared, saved = Config(), Shared(), []
    cfg.save = lambda keys, path=None: saved.extend(keys)
    ok = calibrate.fit(next_hand, shared, cfg, lambda: False, tmp_path / "profile.json")
    return ok, cfg, saved, tmp_path / "profile.json"


STEP = calibrate.READ + calibrate.MEASURE
ALL = [(STEP, hand(fingers=OPEN)), (STEP, hand(fingers=FIST)), (STEP, hand(fingers=THREE)), (60, claw_at(0.5, 0.5))]


def test_fit_puts_every_threshold_between_the_shapes_it_separates(tmp_path):
    ok, cfg, saved, path = fit(ALL, tmp_path)
    assert ok and set(saved) == set(calibrate.FIT_KEYS) and path.exists()
    m = {name: calibrate.measure(h) for name, h in
         (("open", ALL[0][1]), ("fist", ALL[1][1]), ("three", ALL[2][1]), ("claw", ALL[3][1]))}
    assert max(m["fist"][1:5]) < cfg.fist_enter < cfg.fist_exit < min(m["open"][1:5])
    assert m["three"][4] < cfg.three_pinky < cfg.three_pinky_exit < min(m["three"][1:4])
    assert cfg.three_ext < min(m["three"][1:4])
    assert cfg.claw_min < min(m["claw"][1:5]) and max(m["claw"][1:5]) < cfg.claw_max < max(m["open"][1:5])
    assert max(m["claw"][1:4]) < cfg.claw_open < min(m["open"][1:4])
    assert m["open"][5] < cfg.claw_spread < m["claw"][5]


def test_fit_keeps_old_values_for_a_shape_it_could_not_read(tmp_path):
    before = Config()
    script = [(STEP, hand(fingers=OPEN)), (STEP, hand(fingers=OPEN)), (STEP, hand(fingers=THREE)), (60, None)]
    ok, cfg, saved, _ = fit(script, tmp_path)                 # never made a fist, hid the hand for the claw
    assert ok and "three_pinky" in saved
    assert cfg.fist_enter == before.fist_enter and cfg.claw_spread == before.claw_spread


def test_fit_with_no_hand_changes_nothing(tmp_path):
    ok, cfg, saved, _ = fit([(60, None)], tmp_path)
    assert not ok and saved == []
