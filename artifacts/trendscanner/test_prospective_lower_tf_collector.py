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


def test_targets_to_process_catches_up_every_missing_candle():
    step = collector.TIMEFRAME_MS["15m"]
    previous = 100 * step
    latest = 104 * step
    assert collector._targets_to_process(previous, latest, "15m") == [
        101 * step,
        102 * step,
        103 * step,
        104 * step,
    ]


def test_targets_to_process_duplicate_is_noop():
    step = collector.TIMEFRAME_MS["1h"]
    timestamp = 25 * step
    assert collector._targets_to_process(timestamp, timestamp, "1h") == []


def test_targets_to_process_starts_new_version_at_current_candle():
    step = collector.TIMEFRAME_MS["1h"]
    latest = 25 * step
    assert collector._targets_to_process(None, latest, "1h") == [latest]


def test_targets_to_process_respects_catchup_limit():
    step = collector.TIMEFRAME_MS["15m"]
    targets = collector._targets_to_process(
        10 * step,
        20 * step,
        "15m",
        max_catchup=3,
    )
    assert targets == [11 * step, 12 * step, 13 * step]


def test_historical_window_places_target_at_minus_two_without_future_leakage():
    step = collector.TIMEFRAME_MS["15m"]
    source = _frame(230, step=step)
    target = 220 * step

    future_mask = source["time"] > target
    source.loc[future_mask, ["open", "high", "low", "close", "volume"]] = 999999.0

    result = collector._build_window_for_target(source, "15m", target)
    collector._validate_frame(result, "15m", "BTC/USDT", target_timestamp=target)

    assert int(result.iloc[-2]["time"]) == target
    assert int(result.iloc[-1]["time"]) == target + step
    assert result.iloc[-1]["open"] == result.iloc[-2]["close"]
    assert result.iloc[-1]["high"] == result.iloc[-2]["close"]
    assert result.iloc[-1]["low"] == result.iloc[-2]["close"]
    assert result.iloc[-1]["close"] == result.iloc[-2]["close"]
    assert result.iloc[-1]["volume"] == 0.0
    assert 999999.0 not in set(result["close"])


def test_historical_window_fills_bounded_no_tick_gap():
    step = collector.TIMEFRAME_MS["15m"]
    source = _frame(230, step=step)
    target = 220 * step
    missing_timestamp = 150 * step
    source = source[source["time"] != missing_timestamp]

    result = collector._build_window_for_target(source, "15m", target)
    collector._validate_frame(result, "15m", "PENDLE/USDT", target_timestamp=target)
    repaired = result[result["time"] == missing_timestamp].iloc[0]
    assert repaired["volume"] == 0.0
    assert missing_timestamp in result.attrs["filled_no_tick_timestamps"]


def test_historical_window_rejects_too_many_missing_slots():
    step = collector.TIMEFRAME_MS["15m"]
    source = _frame(230, step=step)
    target = 220 * step
    missing = {timestamp * step for timestamp in range(100, 106)}
    source = source[~source["time"].isin(missing)]

    result = collector._build_window_for_target(source, "15m", target)
    assert result.empty
