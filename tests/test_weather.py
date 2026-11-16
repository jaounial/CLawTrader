import pytest

from signal_engine.weather import bin_prob, fit_error_model


def test_bins_partition_to_one():
    mu, s = 52.3, 2.5
    total = (bin_prob("less", None, 49, mu, s)
             + bin_prob("between", 49, 50, mu, s) + bin_prob("between", 51, 52, mu, s)
             + bin_prob("between", 53, 54, mu, s) + bin_prob("between", 55, 56, mu, s)
             + bin_prob("greater", 56, None, mu, s))
    assert total == pytest.approx(1.0, abs=1e-9)


def test_bin_containing_forecast_is_most_likely():
    mu, s = 50.0, 2.0
    assert bin_prob("between", 49, 50, mu, s) > bin_prob("between", 53, 54, mu, s)


def test_fit_removes_bias_and_pools_sigma():
    hist = {"A": [2.0, 2.0, 2.0, 2.0, 2.0, 2.0], "B": [-1.0, 1.0, -1.0, 1.0, -1.0, 1.0]}
    bias, sigma = fit_error_model(hist, shrink=0.0, sigma_floor=0.1)
    assert bias["A"] == pytest.approx(2.0) and bias["B"] == pytest.approx(0.0)
    assert 0.5 < sigma < 1.5


def test_fit_needs_data():
    assert fit_error_model({"A": [1.0, 2.0]}) is None
