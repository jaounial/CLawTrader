import pytest

from signal_engine.edge import ev_per_contract, kelly_fraction, recommend


def test_fee_eats_small_edge():
    # 3-cent raw edge on a 50c contract leaves only 1c after the 2c fee
    assert ev_per_contract(0.53, 0.50) == pytest.approx(0.01)


def test_fee_is_large_on_cheap_contracts():
    # a 5c contract needs >7c win probability just to break even
    assert ev_per_contract(0.07, 0.05) == pytest.approx(0.0)


def test_no_trade_without_edge():
    assert recommend(0.50, 0.51, 0.51, bankroll=500) is None


def test_picks_yes_when_underpriced():
    r = recommend(0.65, yes_ask=0.55, no_ask=0.47, bankroll=500)
    assert r is not None and r.side == "YES" and r.contracts >= 1


def test_picks_no_when_yes_overpriced():
    r = recommend(0.30, yes_ask=0.45, no_ask=0.60, bankroll=500)
    assert r is not None and r.side == "NO"


def test_stake_capped_by_bankroll_fraction():
    r = recommend(0.90, yes_ask=0.40, no_ask=0.62, bankroll=500, max_stake_frac=0.02)
    assert r.cost <= 500 * 0.02 + 1e-9


def test_tiny_bankroll_yields_no_trade_not_fractional():
    assert recommend(0.65, 0.55, 0.47, bankroll=5) is None


def test_kelly_zero_when_negative_ev():
    assert kelly_fraction(0.50, 0.55) == 0.0
