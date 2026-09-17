import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from ready_execution_research import Scenario, compare_periods, evaluate_event

HOUR = 3_600_000
SCENARIO = Scenario(stop_pct=1, target_pct=2, max_bars=3, entry_fee_bps=0,
                    exit_fee_bps=0, slippage_bps=0, funding_cost_bps=0)


def frame(bars, start=0):
    return pd.DataFrame([[start + i * HOUR, *bar] for i, bar in enumerate(bars)],
                        columns=["time", "open", "high", "low", "close"])


def event(direction="LONG", timestamp=0):
    return {"symbol": "BTC/USDT", "timeframe": "1h", "direction": direction,
            "ready_timestamp": timestamp, "entry_reference_price": 99999, "ready_candle_index": 999}


def evaluate(bars, direction="LONG", scenario=SCENARIO, cutoff=10 * HOUR):
    return evaluate_event(event(direction), frame([[50, 51, 49, 50], *bars]), scenario, cutoff)


@pytest.mark.parametrize("direction,bar,price", [
    ("LONG", [100, 103, 99.5, 102], 102),
    ("SHORT", [100, 100.5, 97, 98], 98),
])
def test_entry_is_next_open_and_target_is_directional(direction, bar, price):
    result = evaluate([bar], direction)
    assert result["entry_price"] == 100 and result["entry_timestamp"] == HOUR
    assert result["exit_price"] == price and result["exit_reason"] == "TARGET"
    assert result["net_return_pct"] == pytest.approx(2)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_same_bar_stop_and_target_have_explicit_bounds(direction):
    result = evaluate([[100, 103, 97, 100]], direction)
    assert result["intrabar_ambiguous"] and result["exit_reason"] == "BOTH_TOUCHED_STOP_FIRST"
    assert result["net_return_pct"] == pytest.approx(-1)
    assert result["optimistic_net_return_pct"] == pytest.approx(2)


def test_rally_after_stop_does_not_turn_trade_into_a_win():
    result = evaluate([[100, 100.5, 98, 99], [99, 110, 98, 109]])
    assert result["exit_reason"] == "STOP" and result["bars_held"] == 1
    assert result["net_return_pct"] == pytest.approx(-1)


@pytest.mark.parametrize("direction,bar", [("LONG", [95, 96, 94, 95]), ("SHORT", [105, 106, 104, 105])])
def test_stop_gap_fills_at_worse_open_not_stop_level(direction, bar):
    result = evaluate([[100, 100.5, 99.5, 100], bar], direction)
    assert result["exit_reason"] == "STOP_GAP" and result["bars_held"] == 2
    assert result["net_return_pct"] == pytest.approx(-5)


def test_open_exit_precedes_later_intrabar_range():
    result = evaluate([[100, 100.5, 99.5, 100], [103, 104, 98, 100]])
    assert result["exit_reason"] == "TARGET_GAP" and not result["intrabar_ambiguous"]
    assert result["net_return_pct"] == pytest.approx(3)


@pytest.mark.parametrize("direction,close", [("LONG", 100.5), ("SHORT", 99.5)])
def test_time_exit_uses_last_close(direction, close):
    result = evaluate([[100, 100.6, 99.4, close]], direction, replace(SCENARIO, max_bars=1))
    assert result["exit_reason"] == "TIME_EXIT"
    assert result["net_return_pct"] == pytest.approx(.5)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_costs_use_executed_entry_and_exit_notionals(direction):
    scenario = replace(SCENARIO, max_bars=1, stop_pct=10, target_pct=10,
                       slippage_bps=10, entry_fee_bps=6, exit_fee_bps=8, funding_cost_bps=4)
    result = evaluate([[100, 102, 98, 100]], direction, scenario)
    entry, exit_price = (100.1, 99.9) if direction == "LONG" else (99.9, 100.1)
    gross_cash = exit_price - entry if direction == "LONG" else entry - exit_price
    cost_cash = entry * .0006 + exit_price * .0008 + entry * .0004
    assert result["entry_price"] == pytest.approx(entry)
    assert result["exit_price"] == pytest.approx(exit_price)
    assert result["net_return_pct"] == pytest.approx((gross_cash - cost_cash) / entry * 100)
    assert result["net_return_pct"] < 0


def test_open_future_candle_cannot_resolve_a_trade():
    result = evaluate([[100, 103, 97, 100]], cutoff=2 * HOUR - 1)
    assert result["status"] == "CENSORED" and result["net_return_pct"] is None


def test_early_exit_does_not_require_unobserved_remaining_horizon():
    result = evaluate([[100, 103, 99.5, 102]], cutoff=2 * HOUR)
    assert result["status"] == "RESOLVED"


@pytest.mark.parametrize("change,reason", [("gap", "MISSING_CANDLE"), ("duplicate", "DUPLICATE_TIMESTAMP"), ("bad_price", "INVALID_OHLC")])
def test_invalid_candle_data_never_becomes_a_zero_return(change, reason):
    data = frame([[100, 101, 99, 100], [100, 103, 99.5, 102]])
    if change == "gap":
        data.loc[1, "time"] = 2 * HOUR
    elif change == "duplicate":
        data = pd.concat([data, data.iloc[[1]]], ignore_index=True)
    else:
        data.loc[1, "high"] = float("nan")
    result = evaluate_event(event(), data, SCENARIO, 5 * HOUR)
    assert result["status"] == "INVALID" and result["reason"] == reason
    assert result["net_return_pct"] is None


def test_future_prices_after_exit_do_not_affect_result_and_inputs_are_immutable():
    data = frame([[100, 101, 99, 100], [100, 103, 99.5, 102], [102, 104, 101, 103]])
    saved_event, original = event(), data.copy(deep=True)
    before = copy.deepcopy(saved_event)
    result = evaluate_event(saved_event, data, SCENARIO, 10 * HOUR)
    pd.testing.assert_frame_equal(data, original)
    assert saved_event == before
    data.loc[2, ["open", "high", "low", "close"]] = [1, 1e6, .01, 1]
    assert evaluate_event(saved_event, data, SCENARIO, 10 * HOUR) == result


def payload(events):
    return {"runs": [{"status": "OK", "events": events}]}


def test_period_reports_preserve_missing_data_and_never_pool_performance():
    current, holdout = payload([event(timestamp=10 * HOUR)]), payload([event()])
    frames = {("CURRENT", "BTC/USDT", "1h"): frame([[100, 101, 99, 100], [100, 103, 99.5, 102]], 10 * HOUR)}
    report = compare_periods(current, holdout, frames, SCENARIO, 20 * HOUR)
    assert report["summary"]["status_counts"] == {"RESOLVED": 1, "INVALID": 1}
    assert "mean_net_return_pct" not in report["summary"]
    groups = {(row["period"], row["direction"]): row for row in report["by_period_direction"]}
    assert groups[("CURRENT", "LONG")]["mean_net_return_pct"] == pytest.approx(2)
    assert groups[("HOLDOUT", "LONG")]["mean_net_return_pct"] is None
    json.dumps(report, allow_nan=False)


def test_holdout_outcome_horizon_cannot_overlap_current():
    with pytest.raises(ValueError, match="including outcome horizons"):
        compare_periods(payload([event(timestamp=3 * HOUR)]), payload([event()]), {}, SCENARIO, 20 * HOUR)


def test_duplicate_ready_events_are_not_double_counted():
    with pytest.raises(ValueError, match="Duplicate READY"):
        compare_periods(payload([event(), event()]), payload([]), {}, SCENARIO, 20 * HOUR)


@pytest.mark.parametrize("changes", [{"stop_pct": 0}, {"target_pct": 100}, {"max_bars": True},
                                    {"entry_fee_bps": -1}, {"slippage_bps": 10000}, {"funding_cost_bps": float("nan")}])
def test_invalid_or_implicit_scenario_assumptions_are_rejected(changes):
    with pytest.raises(ValueError):
        replace(SCENARIO, **changes)


def test_cli_consumes_saved_outcomes_and_manifest_without_network(tmp_path):
    current, holdout = payload([event(timestamp=10 * HOUR)]), payload([event()])
    (tmp_path / "current.json").write_text(json.dumps(current))
    (tmp_path / "holdout.json").write_text(json.dumps(holdout))
    (tmp_path / "scenario.json").write_text(json.dumps(SCENARIO.__dict__))
    manifest = []
    for period, start in (("CURRENT", 10 * HOUR), ("HOLDOUT", 0)):
        name = period.lower() + ".csv"
        frame([[100, 101, 99, 100], [100, 103, 99.5, 102]], start).to_csv(tmp_path / name, index=False)
        manifest.append({"period": period, "symbol": "BTC/USDT", "timeframe": "1h", "path": name})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "report.json"
    command = [sys.executable, str(Path(__file__).with_name("ready_execution_research.py")),
               "--current", str(tmp_path / "current.json"), "--holdout", str(tmp_path / "holdout.json"),
               "--manifest", str(tmp_path / "manifest.json"), "--scenario", str(tmp_path / "scenario.json"),
               "--as-of-ms", str(20 * HOUR), "--output", str(output)]
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    assert report["summary"]["resolved_events"] == 2
    assert {row["period"] for row in report["events"]} == {"CURRENT", "HOLDOUT"}
    # Missing data must remain visible and make the CLI report incomplete coverage.
    (tmp_path / "manifest.json").write_text(json.dumps(manifest[:1]))
    run = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert run.returncode == 2, run.stderr
    assert json.loads(output.read_text())["summary"]["status_counts"] == {"RESOLVED": 1, "INVALID": 1}
