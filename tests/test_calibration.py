from signal_engine.calibration import bucket_stats, purchases


def _r(bid, ask, yes, close=1, group="g"):
    return dict(group=group, close=close, bid=bid, ask=ask, yes=yes)


def test_both_sides_are_scored_with_fee():
    ps = list(purchases([_r(0.40, 0.42, True)], fee=0.02))
    # YES at 0.42 wins: 1 - 0.42 - 0.02 ; NO at 0.60 loses: 0 - 0.60 - 0.02
    assert abs(ps[0][2] - 0.56) < 1e-9 and abs(ps[1][2] + 0.62) < 1e-9


def test_efficient_prices_lose_the_fee():
    # 100 contracts priced 0.50 that win exactly half the time -> mean pnl = -fee
    rows = [_r(0.49, 0.50, i % 2 == 0, close=i // 10) for i in range(100)]
    yes_side = [b for b in bucket_stats(rows) if b["lo"] == 0.50][0]
    assert abs(yes_side["win"] - 0.5) < 0.02 and yes_side["mean_pnl"] < 0


def test_win_rate_recovers_from_pnl():
    rows = [_r(0.05, 0.06, False, close=i) for i in range(20)]  # cheap YES never wins
    b = [x for x in bucket_stats(rows) if x["lo"] == 0.05][0]
    assert abs(b["win"]) < 1e-9
