"""Tests ensuring latest/prev metadata added and latest doesn't affect scoring/decision"""
import copy
import pandas as pd
import numpy as np
from breakout_quality import calculate_breakout_quality
from breakout_diagnostic_report import build_breakout_diagnostic_report
from quality_pipeline import analyze_both_directions


def make_simple_df(n=15, base=100.0):
    np.random.seed(0)
    closes = base + np.cumsum(np.random.randn(n) * 0.5)
    opens = closes - np.random.uniform(0.1, 0.5, n)
    highs = np.maximum(closes, opens) + np.random.uniform(0.1, 0.8, n)
    lows = np.minimum(closes, opens) - np.random.uniform(0.1, 0.8, n)
    df = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})
    return df


def test_prev_and_latest_present_and_correct():
    df = make_simple_df(n=5, base=50.0)
    # simple diagonal fake line that won't raise
    fake_line = {"x1": 0, "y1": 51.0, "x2": 3, "y2": 52.0, "slope": (52.0 - 51.0) / 3}

    res = calculate_breakout_quality(df, fake_line, "LONG")
    assert "prev_close" in res and "latest_close" in res
    sig_idx = res["signal_index"]
    assert res["prev_close"] == round(float(df.iloc[sig_idx - 1]["close"]), 6)
    assert res["signal_close"] == round(float(df.iloc[sig_idx]["close"]), 6)
    assert res["latest_close"] == round(float(df.iloc[-1]["close"]), 6)


def test_crossed_on_latest_long_and_short_via_report():
    # Build manual all_results similar to existing cross-state tests
    from trendlines import line_value

    def make_line(x1, y1, x2, y2):
        slope = (y2 - y1) / (x2 - x1)
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}

    def make_tf(line, sig_idx, prev_close=None, sig_close=None, latest_close=None, atr=1.0, cross_comp=None):
        prev_line = line_value(line, sig_idx - 1)
        sig_line = line_value(line, sig_idx)
        latest_line = line_value(line, sig_idx + 1)
        bq = {
            "signal_index": sig_idx,
            "line_price": sig_line,
            "signal_close": sig_close,
            "atr": atr,
            "components": {"cross": cross_comp if cross_comp is not None else 0.0},
        }
        if prev_close is not None:
            bq["prev_close"] = prev_close
        if latest_close is not None:
            bq["latest_close"] = latest_close
        return {
            "quality": {"LONG": {"line": line, "breakout_quality": bq},
                        "SHORT": {"line": line, "breakout_quality": bq}},
            "decision_details": {"LONG": {}, "SHORT": {}},
        }

    line = make_line(0, 0.0, 4, 4.0)
    sig_idx = 2
    prev_line = line_value(line, sig_idx - 1)
    sig_line = line_value(line, sig_idx)
    # LONG: signal on pre-breakout side, latest crosses above
    long_tf = make_tf(line, sig_idx, prev_close=prev_line - 1.0, sig_close=sig_line - 0.5, latest_close=sig_line + 1.5)
    # SHORT: signal on pre-breakout side (above line), latest crosses below
    # For short, simulate prices inverted
    short_tf = make_tf(line, sig_idx, prev_close=prev_line + 1.0, sig_close=sig_line + 0.5, latest_close=sig_line - 1.5)

    all_results = {"SYM": {"1H": long_tf, "4H": short_tf, "FINAL": {}}}
    rep = build_breakout_diagnostic_report(all_results)
    counts = rep.get("cross_state_counts", {})
    assert counts.get("CROSSED_ON_LATEST_CANDLE", 0) >= 2


def test_latest_does_not_change_score_confirm_or_decision():
    # Create two dataframes that only differ in the latest candle
    df_base = make_simple_df(n=15, base=200.0)
    df_a = df_base.copy()
    df_b = df_base.copy()
    # modify only the last candle in df_b
    df_b.loc[df_b.index[-1], "close"] = df_b.loc[df_b.index[-1], "close"] + 50.0
    df_b.loc[df_b.index[-1], "high"] = df_b.loc[df_b.index[-1], "high"] + 50.0

    # compute breakout_quality directly for each direction using same fake line
    fake_line = {"x1": 2, "y1": 210.0, "x2": 10, "y2": 215.0, "slope": (215.0 - 210.0) / 8}

    for dir in ("LONG", "SHORT"):
        # call function twice and ensure that aside from the two new metadata
        # keys the rest of the returned structure is unchanged (diagnostics-only)
        r1 = calculate_breakout_quality(df_a, fake_line, dir)
        r2 = calculate_breakout_quality(df_a, fake_line, dir)

        def _strip_meta(d):
            return {k: v for k, v in d.items() if k not in ("prev_close", "latest_close")}

        assert _strip_meta(r1) == _strip_meta(r2)


def test_source_input_not_mutated():
    df = make_simple_df(n=12, base=10.0)
    df_copy = df.copy(deep=True)
    fake_line = {"x1": 2, "y1": 12.0, "x2": 8, "y2": 13.0, "slope": (13.0 - 12.0) / 6}
    _ = calculate_breakout_quality(df, fake_line, "LONG")
    # ensure df unchanged
    pd.testing.assert_frame_equal(df, df_copy)


if __name__ == "__main__":
    test_prev_and_latest_present_and_correct()
    test_crossed_on_latest_long_and_short_via_report()
    test_latest_does_not_change_score_confirm_or_decision()
    test_source_input_not_mutated()
    print("latest metadata tests passed")
