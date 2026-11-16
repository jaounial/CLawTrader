import math

import pytest

from signal_engine.prob import above_prob, realized_sigma, touch_below_prob


def test_atm_is_about_half():
    assert above_prob(100, 100, 0.001, 10) == pytest.approx(0.5, abs=0.01)


def test_monotone_in_strike():
    ps = [above_prob(100, k, 0.001, 30) for k in (98, 99, 100, 101, 102)]
    assert ps == sorted(ps, reverse=True)


def test_expired_is_deterministic():
    assert above_prob(101, 100, 0.001, 0) == 1.0
    assert above_prob(99, 100, 0.001, 0) == 0.0


def test_touch_is_twice_above_in_the_tail():
    # for a driftless walk, P(touch lower barrier) ~ 2 * P(end below barrier)
    below = 1 - above_prob(100, 90, 0.01, 100)
    assert touch_below_prob(100, 90, 0.01, 100) == pytest.approx(2 * below, rel=0.15)


def test_touch_already_below():
    assert touch_below_prob(89, 90, 0.01, 10) == 1.0


def test_realized_sigma_constant_growth_is_zero():
    closes = [100 * math.exp(0.001 * i) for i in range(10)]
    assert realized_sigma(closes) == pytest.approx(0.0, abs=1e-12)
