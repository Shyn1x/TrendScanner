"""Tests for 4h Donchian prospective shadow outcome mechanics."""
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).with_name("regime_donchian_shadow.py")
spec = importlib.util.spec_from_file_location("regime_donchian_shadow", MODULE)
shadow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shadow)


def candle(op, hi, lo, close):
    return {"open": op, "high": hi, "low": lo, "close": close}


def test_next_bar_open_and_both_sides_costs():
    long = shadow.outcome("LONG", 95, [candle(100, 110, 99, 108)], 1)
    short = shadow.outcome("SHORT", 105, [candle(100, 101, 90, 92)], 1)
    assert long["net_return_pct"] == pytest.approx(7.84)
    assert short["net_return_pct"] == pytest.approx(7.84)
    assert long["funding_included"] is False


def test_stop_uses_worst_open_when_gapped_past_stop():
    a = shadow.outcome("LONG", 95, [candle(92, 94, 90, 93)], 1)
    b = shadow.outcome("SHORT", 105, [candle(109, 111, 106, 110)], 1)
    assert a["status"] == "INVALID_STOP_AT_NEXT_OPEN"
    assert b["status"] == "INVALID_STOP_AT_NEXT_OPEN"
    c = shadow.outcome("LONG", 95, [candle(100, 102, 94, 98),
                                     candle(92, 94, 90, 93)], 2)
    assert c["stop_hit"]
    assert c["exit_price"] == 95


def test_stop_hit_long_and_short():
    long = shadow.outcome("LONG", 95, [candle(100, 102, 94, 101)], 1)
    short = shadow.outcome("SHORT", 105, [candle(100, 106, 98, 99)], 1)
    assert long["stop_hit"] and long["exit_price"] == 95
    assert short["stop_hit"] and short["exit_price"] == 105


def test_invalid_horizon_or_direction_rejected():
    with pytest.raises(ValueError):
        shadow.outcome("LONG", 95, [], 1)
    with pytest.raises(ValueError):
        shadow.outcome("SIDEWAYS", 95, [candle(100, 101, 99, 100)], 1)


def test_last_closed_target_is_strict():
    tf = shadow.TIMEFRAME_MS
    assert shadow.latest_target(12 * tf + 5000) == 11 * tf
    assert shadow.latest_target(12 * tf) == 11 * tf


def test_collector_is_independent_of_order_execution():
    source = MODULE.read_text()
    for forbidden in ("order/create", "bybit_demo_executor", "api-demo.bybit.com",
                      "DELETE FROM", "DROP TABLE", "UPDATE bybit_demo"):
        assert forbidden not in source
