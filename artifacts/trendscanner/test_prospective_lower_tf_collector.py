import pandas as pd

import prospective_lower_tf_collector as collector


COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _frame(count, *, step=900_000, start=0):
    rows = [
        [start + index * step, 1.0, 1.0, 1.0, 1.0, 1.0]
        for index in range(count)
    ]
    return pd.DataFrame(rows, columns=COLUMNS)


def test_fixed200_uses_normal_fetch_when_complete(monkeypatch):
    expected = _frame(200)
    monkeypatch.setattr(collector, "get_data", lambda symbol, timeframe: expected)

    def unexpected_fallback(*args, **kwargs):
        raise AssertionError("fallback should not run")

    monkeypatch.setattr(collector, "_fetch_recent_ohlcv", unexpected_fallback)
    result = collector._get_fixed200_data("BTC/USDT", "15m")
    assert len(result) == 200
    assert result is expected


def test_fixed200_retries_short_exchange_response(monkeypatch):
    monkeypatch.setattr(collector, "get_data", lambda symbol, timeframe: _frame(199))
    fallback = _frame(205).values.tolist()
    monkeypatch.setattr(
        collector,
        "_fetch_recent_ohlcv",
        lambda *args, **kwargs: fallback,
    )
    monkeypatch.setattr(collector, "_to_futures_symbol", lambda symbol: symbol)

    result = collector._get_fixed200_data("PENDLE/USDT", "15m")
    assert len(result) == 200
    assert int(result.iloc[0]["time"]) == 5 * 900_000
    assert int(result.iloc[-1]["time"]) == 204 * 900_000


def test_fixed200_still_rejects_genuinely_short_history(monkeypatch):
    monkeypatch.setattr(collector, "get_data", lambda symbol, timeframe: _frame(150))
    fallback = _frame(180).values.tolist()
    monkeypatch.setattr(
        collector,
        "_fetch_recent_ohlcv",
        lambda *args, **kwargs: fallback,
    )
    monkeypatch.setattr(collector, "_to_futures_symbol", lambda symbol: symbol)

    result = collector._get_fixed200_data("ETC/USDT", "15m")
    try:
        collector._validate_frame(result, "15m", "ETC/USDT")
    except ValueError as exc:
        assert "EXPECTED_200_BARS" in str(exc)
    else:
        raise AssertionError("short history must remain rejected")
