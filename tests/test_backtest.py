from signal_engine.backtest_btc import evaluate


def _row(K, yes, bid=0.40, ask=0.42):
    return dict(spot=100.0, K=K, sig={"15": 0.001}, bid=bid, ask=ask, yes=yes, minutes=9.5)


def test_pnl_accounts_for_fee_and_side():
    # model p(above 90) ~ 1.0, market asks 0.42 -> buys YES; wins -> 1 - 0.42 - 0.02
    r = evaluate([_row(90.0, True)], 15, 0.03)
    assert r["n"] == 1 and abs(r["mean_pnl"] - (1 - 0.42 - 0.02)) < 1e-9


def test_loss_is_full_price_plus_fee():
    r = evaluate([_row(90.0, False)], 15, 0.03)
    assert abs(r["mean_pnl"] - (0 - 0.42 - 0.02)) < 1e-9


def test_no_trade_when_market_agrees():
    # strike at spot -> p ~ 0.5, market 0.49/0.51 -> no edge after fee
    r = evaluate([_row(100.0, True, bid=0.49, ask=0.51)], 15, 0.03)
    assert r["n"] == 0
