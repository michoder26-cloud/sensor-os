"""Head offset maths (the detector itself needs a camera)."""
from sensor_os.head import to_offset


def test_centre_is_straight_ahead():
    assert to_offset(0.5, 0.5, 1.6) == (0.0, 0.0)


def test_right_and_down_are_positive_and_clamped():
    hx, hy = to_offset(0.7, 0.6, 1.6)
    assert hx > 0 and hy > 0 and hx > hy
    assert to_offset(1.0, 0.0, 1.6) == (1.0, -1.0)
