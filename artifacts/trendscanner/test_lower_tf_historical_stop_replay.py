import pandas as pd
import pytest

import lower_tf_historical_stop_replay as replay


COLUMNS = ["time", "open", "high", "low", "close", "volume"]


def _frame(count=300, *, step=900_000, start=0):
    rows = []
    for index in range(count):
        close = 100.0 + index * 0.01
        rows.append([start + index * step, close, close + 0.2, close - 0.2, close, 10.0])
    return pd.DataFrame(rows, columns=COLUMNS)


def _analyze(window):
    return {"trend": "UP", "target": int(window.iloc[-2]["time"])}


def _evaluator(states):
    def evaluate(symbol, timeframe, direction, result):
        ready = states.get((result["target"], direction), False)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "ready_timestamp": result["target"],
            "ready": ready,
        }

    return evaluate


def test_target_grid_reserves_history_but_split_reserves_future_outcomes():
    source = _frame(300)
    targets = replay.eligible_target_timestamps(source, "15m")
    assert targets[0] == 198 * 900_000
    assert targets[-1] == 299 * 900_000

    splits = replay.build_temporal_splits(targets)
    assert [item["name"] for item in splits] == list(replay.SPLIT_NAMES)
    assert sum(item["target_count"] for item in splits) == len(targets)
    assert splits[0]["end_timestamp"] < splits[1]["start_timestamp"]
    assert splits[1]["end_timestamp"] < splits[2]["start_timestamp"]


def test_first_ready_is_baseline_and_only_false_to_true_is_event():
    source = _frame(240)
    step = replay.TIMEFRAME_MS["15m"]
    targets = list(range(198 * step, 220 * step, step))
    split = {
        "name": "DEVELOPMENT",
        "start_timestamp": targets[0],
        "end_timestamp": targets[-1],
        "target_count": len(targets),
        "targets": targets,
    }
    states = {
        (targets[0], "LONG"): True,
        (targets[1], "LONG"): True,
        (targets[2], "LONG"): False,
        (targets[3], "LONG"): True,
        (targets[4], "LONG"): True,
    }

    result = replay.replay_split(
        source,
        symbol="BTC/USDT",
        timeframe="15m",
        split=split,
        analyze_fn=_analyze,
        evaluate_fn=_evaluator(states),
    )

    assert result["errors"] == []
    assert [event["ready_timestamp"] for event in result["events"]] == [targets[3]]
    event = result["events"][0]
    assert len(event["raw_outcomes"]) == len(replay.HORIZONS)
    assert len(event["stop_outcomes"]) == len(replay.HORIZONS) * len(replay.STOP_LOSS_BPS)
    assert all(row["horizon_bars"] in replay.HORIZONS for row in event["stop_outcomes"])


def test_partition_baselines_are_independent():
    source = _frame(300)

    def always_ready(symbol, timeframe, direction, result):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "ready_timestamp": result["target"],
            "ready": True,
        }

    result = replay.replay_symbol(
        source,
        symbol="ETH/USDT",
        timeframe="15m",
        analyze_fn=_analyze,
        evaluate_fn=always_ready,
    )
    assert [len(split["events"]) for split in result["splits"]] == [0, 0, 0]
    assert all(split["evaluated_targets"] > 1 for split in result["splits"])


def test_outcomes_never_cross_partition_boundary():
    source = _frame(240)
    step = replay.TIMEFRAME_MS["15m"]
    targets = list(range(198 * step, 220 * step, step))
    split = {
        "name": "HOLDOUT_A",
        "start_timestamp": targets[0],
        "end_timestamp": targets[-1],
        "target_count": len(targets),
        "targets": targets,
    }
    states = {}
    # This transition is inside the 12-bar boundary purge and must not be seen.
    states[(targets[-2], "LONG")] = True
    result = replay.replay_split(
        source,
        symbol="SOL/USDT",
        timeframe="15m",
        split=split,
        analyze_fn=_analyze,
        evaluate_fn=_evaluator(states),
    )
    assert result["events"] == []
    assert result["evaluated_targets"] == len(targets) - replay.MAX_HORIZON


def test_report_aggregates_stop_adjusted_results():
    event = {
        "split": "HOLDOUT_B",
        "timeframe": "1h",
        "direction": "LONG",
        "raw_outcomes": [{"horizon_bars": 1, "close_return_pct": 2.0}],
        "stop_outcomes": [{
            "horizon_bars": 1,
            "stop_loss_bps": 100,
            "stop_hit": True,
            "stop_adjusted_return_pct": -1.0,
        }],
    }
    rows = replay.aggregate_events([event])
    expected = {
        "split": "HOLDOUT_B",
        "timeframe": "1h",
        "direction": "LONG",
        "horizon_bars": 1,
        "stop_loss_bps": 100,
        "n": 1,
        "stop_hits": 1,
        "stop_hit_rate_pct": 100.0,
        "avg_raw_close_return_pct": 2.0,
        "raw_positive_rate_pct": 100.0,
        "avg_stop_adjusted_return_pct": -1.0,
        "median_stop_adjusted_return_pct": -1.0,
        "stop_adjusted_positive_rate_pct": 0.0,
        "round_trip_cost_pct": 0.16,
        "avg_net_return_pct": -1.16,
        "median_net_return_pct": -1.16,
        "net_positive_rate_pct": 0.0,
        "avg_return_r": -1.16,
        "avg_isolated_pnl_at_starting_risk": -3.48,
    }
    assert rows[0] == pytest.approx(expected)


def test_execution_costs_and_risk_sizing_are_explicit():
    metrics = replay._execution_metrics(
        0.5,
        100,
        fee_bps_per_side=6.0,
        slippage_bps_per_side=2.0,
        starting_balance=300.0,
        risk_pct=1.0,
    )
    assert metrics == pytest.approx({
        "round_trip_cost_pct": 0.16,
        "net_return_pct": 0.34,
        "return_r": 0.34,
        "isolated_pnl_at_starting_risk": 1.02,
    })
