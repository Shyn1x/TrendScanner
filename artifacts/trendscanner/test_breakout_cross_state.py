"""Tests for breakout cross-state classification in breakout_diagnostic_report"""
import copy
from breakout_diagnostic_report import build_breakout_diagnostic_report
from trendlines import line_value


def make_line(x1, y1, x2, y2):
    slope = (y2 - y1) / (x2 - x1)
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "slope": slope}


def make_tf(line, sig_idx, prev_close=None, sig_close=None, latest_close=None, atr=1.0, cross_comp=None):
    # compute line prices for convenience
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
        "quality": {
            "LONG": {"line": line, "breakout_quality": bq},
            "SHORT": {"line": line, "breakout_quality": bq},
        },
        "decision_details": {"LONG": {}, "SHORT": {}},
    }


def test_all_states_and_no_mutation():
    # create simple diagonal line y=x
    line = make_line(0, 0.0, 4, 4.0)

    # signal index 2 -> prev idx 1, latest idx 3
    sig_idx = 2

    # FRESH_CROSS (prev <= prev_line, signal > signal_line)
    prev_line = line_value(line, sig_idx - 1)
    sig_line = line_value(line, sig_idx)
    fresh = make_tf(line, sig_idx, prev_close=prev_line, sig_close=sig_line + 1.0, atr=1.0, cross_comp=30.0)

    # ALREADY_BEYOND_LINE (both strictly beyond)
    already = make_tf(line, sig_idx, prev_close=prev_line + 1.0, sig_close=sig_line + 2.0, atr=1.0, cross_comp=0.0)

    # NOT_REACHED_LINE (both on pre-breakout side, strictly)
    not_reached = make_tf(line, sig_idx, prev_close=prev_line - 1.0, sig_close=sig_line - 0.5, atr=1.0, cross_comp=0.0)

    # CROSSED_BACK (prev beyond, signal on other side)
    crossed_back = make_tf(line, sig_idx, prev_close=prev_line + 1.0, sig_close=sig_line - 1.0, atr=1.0, cross_comp=0.0)

    # TOUCH_OR_EQUAL (signal == line)
    touch = make_tf(line, sig_idx, prev_close=prev_line - 1.0, sig_close=sig_line, atr=1.0, cross_comp=0.0)

    # CROSSED_ON_LATEST_CANDLE (signal not crossing but latest would)
    crossed_latest = make_tf(line, sig_idx, prev_close=prev_line - 1.0, sig_close=sig_line - 0.5, latest_close=sig_line + 1.5, atr=1.0, cross_comp=0.0)

    # UNKNOWN (insufficient data: no closes)
    unknown = make_tf(line, sig_idx, prev_close=None, sig_close=None, latest_close=None, atr=1.0, cross_comp=0.0)

    all_results = {
        "SYM": {
            "1H": fresh,
            "4H": already,
            "1D": not_reached,
            "1W": crossed_back,
            "1M": touch,
            "15m": crossed_latest,
            "30m": unknown,
            "FINAL": {},
        }
    }

    original = copy.deepcopy(all_results)
    rep = build_breakout_diagnostic_report(all_results)

    counts = rep.get("cross_state_counts", {})
    # assert each expected state present
    assert counts.get("FRESH_CROSS", 0) >= 1
    assert counts.get("ALREADY_BEYOND_LINE", 0) >= 1
    assert counts.get("NOT_REACHED_LINE", 0) >= 1
    assert counts.get("CROSSED_BACK", 0) >= 1
    assert counts.get("TOUCH_OR_EQUAL", 0) >= 1
    assert counts.get("CROSSED_ON_LATEST_CANDLE", 0) >= 1
    assert counts.get("UNKNOWN", 0) >= 1

    # examples lists
    ex_al = rep.get("examples_already_beyond", [])
    ex_cl = rep.get("examples_crossed_on_latest", [])
    assert isinstance(ex_al, list)
    assert isinstance(ex_cl, list)

    # source not mutated
    assert all_results == original

    print("breakout_cross_state tests passed")


if __name__ == "__main__":
    test_all_states_and_no_mutation()
