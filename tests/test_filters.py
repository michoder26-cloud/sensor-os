import random
import statistics

from sensor_os.filters import OneEuroFilter


def test_first_value_passes_through():
    f = OneEuroFilter(1.0, 0.0)
    assert f(0.7, 0.0) == 0.7


def test_converges_to_constant():
    f = OneEuroFilter(1.0, 0.0)
    f(0.0, 0.0)
    out = 0.0
    for i in range(1, 200):
        out = f(1.0, i / 30)
    assert abs(out - 1.0) < 1e-3


def test_reduces_jitter_at_rest():
    rnd = random.Random(1)
    f = OneEuroFilter(1.2, 10.0)
    raw, smooth = [], []
    for i in range(300):
        x = 0.5 + rnd.gauss(0, 0.003)
        raw.append(x)
        smooth.append(f(x, i / 30))
    assert statistics.pstdev(smooth[30:]) < statistics.pstdev(raw[30:]) / 2


def test_non_increasing_time_is_ignored():
    f = OneEuroFilter(1.0, 0.0)
    f(0.0, 1.0)
    assert f(5.0, 1.0) == 0.0
