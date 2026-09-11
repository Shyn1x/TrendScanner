import pandas as pd

import prospective_lower_tf_collector as collector


COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _frame(count, *, step=900_000, start=0):
    rows = [
        [start + index * step, 1.0, 1.0, 1.0, float(index + 1), 1.0]
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


def test_fixed200_fills_bounded_no_tick_gap(monkeypatch):
    source = _frame(205)
    missing_timestamp = 100 * 900_000
    source = source[source["time"] != missing_timestamp]
    normal = source.tail(199).copy()
    monkeypatch.setattr(collector, "get_data", lambda symbol, timeframe: normal)
    monkeypatch.setattr(
        collector,
        "_fetch_recent_ohlcv",
        lambda *args, **kwargs: source.values.tolist(),
    )
    monkeypatch.setattr(collector, "_to_futures_symbol", lambda symbol: symbol)

    result = collector._get_fixed200_data("PENDLE/USDT", "15m")
    collector._validate_frame(result, "15m", "PENDLE/USDT")
    repaired = result[result["time"] == missing_timestamp].iloc[0]
    assert repaired["open"] == 100.0
    assert repaired["high"] == 100.0
    assert repaired["low"] == 100.0
    assert repaired["close"] == 100.0
    assert repaired["volume"] == 0.0
    assert result.attrs["filled_no_tick_timestamps"] == [missing_timestamp]


def test_fixed200_rejects_gap_larger_than_fill_bound(monkeypatch):
    source = _frame(210)
    missing = {timestamp * 900_000 for timestamp in range(100, 106)}
    source = source[~source["time"].isin(missing)]
    monkeypatch.setattr(collector, "get_data", lambda symbol, timeframe: source.tail(199))
    monkeypatch.setattr(
        collector,
        "_fetch_recent_ohlcv",
        lambda *args, **kwargs: source.values.tolist(),
    )
    monkeypatch.setattr(collector, "_to_futures_symbol", lambda symbol: symbol)

    result = collector._get_fixed200_data("ETC/USDT", "15m")
    try:
        collector._validate_frame(result, "15m", "ETC/USDT")
    except ValueError as exc:
        assert "NONCONTIGUOUS_TIME_GRID" in str(exc)
    else:
        raise AssertionError("large gaps must remain rejected")


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
