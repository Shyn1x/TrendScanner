"""Contract tests for the isolated regime-gated Donchian research candidate."""
import importlib.util
from pathlib import Path

path = Path(__file__).with_name("regime_donchian_research.py")
spec = importlib.util.spec_from_file_location("regime_donchian_research", path)
strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy)


def bars(n=30, close=100.0):
    step = strategy.TIMEFRAME_MS
    return [{"time": i * step, "open": close, "high": close + 1,
             "low": close - 1, "close": close} for i in range(n)]


def test_bull_breakout_uses_only_prior_channel_and_outputs_research_flag():
    candles = bars()
    candles[-1].update(open=102, high=110, low=101, close=109)
    outcome = strategy.evaluate(candles, "BULL", candles[-1]["time"])
    assert outcome["signal"] == "ENTER_LONG"
    assert outcome["upper_20"] == 101
    assert outcome["research_only"] is True
    assert outcome["initial_stop_reference"] < 109


def test_current_high_alone_is_not_entry():
    candles = bars()
    candles[-1].update(high=110, close=100)
    assert strategy.evaluate(candles, "BULL", candles[-1]["time"])["signal"] == "NO_TRADE"


def test_regime_gate_and_stale_or_missing_context_fail_closed():
    candles = bars()
    candles[-1].update(open=102, high=110, low=101, close=109)
    for regime in ("BEAR", "MIXED"):
        assert strategy.evaluate(candles, regime, candles[-1]["time"])["signal"] not in ("ENTER_LONG", "ENTER_SHORT")
    assert strategy.evaluate(candles, "UNKNOWN", candles[-1]["time"])["signal"] == "NO_TRADE"
    assert strategy.evaluate(candles, "BULL", candles[-2]["time"])["reason"] == "REGIME_TIMESTAMP_MISMATCH"


def test_breakdown_exit_even_in_bear():
    candles = bars()
    candles[-1].update(open=98, high=99, low=92, close=93)
    assert strategy.evaluate(candles, "BEAR", candles[-1]["time"])["signal"] == "ENTER_SHORT"


def test_gap_blocks_signals_and_short_history_is_explicit():
    candles = bars()
    assert strategy.evaluate(candles[:10], "BULL", candles[9]["time"])["reason"] == "INSUFFICIENT_HISTORY"
    candles[8]["time"] += strategy.TIMEFRAME_MS
    assert strategy.evaluate(candles, "BULL", candles[-1]["time"])["reason"] == "GAPPED_OR_DUPLICATE_CANDLES"


def test_no_order_or_network_dependencies():
    code = path.read_text()
    for forbidden in ("requests", "urllib", "api-demo", "order/create", "database_url", "connect("):
        assert forbidden not in code.lower()


def test_bear_short_breakdown_and_conservative_atr_stop():
    candles = bars()
    candles[-1].update(open=98, high=99, low=90, close=91)
    result = strategy.evaluate(candles, "BEAR", candles[-1]["time"])
    assert result["signal"] == "ENTER_SHORT"
    assert result["initial_stop_reference"] > 91
    assert result["exit_long"] is True


def test_mixed_can_emit_exit_advisory_without_new_entry():
    candles = bars()
    candles[-1].update(open=98, high=99, low=92, close=93)
    result = strategy.evaluate(candles, "MIXED", candles[-1]["time"])
    assert result["signal"] == "EXIT_LONG"
    assert result["exit_long"]
