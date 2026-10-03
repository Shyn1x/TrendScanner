import pytest

import lower_tf_portfolio_replay as portfolio


STEP = portfolio.TIMEFRAME_MS["1h"]


def _event(symbol, timestamp, *, split="HOLDOUT_A", return_pct=1.0, stop_hit=False, bars_to_stop=None):
    return {
        "split": split,
        "symbol": symbol,
        "timeframe": "1h",
        "direction": "LONG",
        "ready_timestamp": timestamp,
        "stop_outcomes": [
            {
                "horizon_bars": 12,
                "stop_loss_bps": stop,
                "stop_hit": stop_hit,
                "bars_to_stop": bars_to_stop,
                "stop_adjusted_return_pct": -stop / 100.0 if stop_hit else return_pct,
            }
            for stop in (100, 150)
        ],
    }


def _simulate(events, **overrides):
    assumptions = {
        "stop_loss_bps": 100,
        "starting_balance": 300.0,
        "risk_pct": 1.0,
        "max_total_risk_pct": 3.0,
        "max_open_positions": 3,
        "max_leverage": 3.0,
        "fee_bps_per_side": 0.0,
        "slippage_bps_per_side": 0.0,
    }
    assumptions.update(overrides)
    return portfolio.simulate_portfolio(events, **assumptions)


def test_chronological_compounding_uses_balance_after_prior_exit():
    events = [
        _event("BTC/USDT", 0, return_pct=1.0),
        _event("ETH/USDT", 13 * STEP, return_pct=1.0),
    ]
    result = _simulate(events)
    assert result["entered_trades"] == 2
    assert result["trades"][0]["risk_amount"] == pytest.approx(3.0)
    assert result["trades"][0]["pnl"] == pytest.approx(3.0)
    assert result["trades"][1]["risk_amount"] == pytest.approx(3.03)
    assert result["final_balance"] == pytest.approx(306.03)


def test_three_percent_risk_and_three_x_leverage_allow_only_three_concurrent_positions():
    events = [_event(f"COIN{index}/USDT", 0) for index in range(4)]
    result = _simulate(events)
    assert result["entered_trades"] == 3
    assert result["skipped_events"] == {"max_open_positions": 1}
    assert result["max_concurrent_positions"] == 3
    assert result["max_notional_to_balance"] == pytest.approx(3.0)


def test_duplicate_symbol_is_not_opened_twice():
    events = [_event("BTC/USDT", 0), _event("BTC/USDT", STEP)]
    result = _simulate(events)
    assert result["entered_trades"] == 1
    assert result["skipped_events"] == {"duplicate_symbol": 1}


def test_exit_at_timestamp_settles_before_new_entry():
    events = [
        _event("BTC/USDT", 0, return_pct=0.0, stop_hit=True, bars_to_stop=1),
        _event("BTC/USDT", STEP, return_pct=1.0),
    ]
    result = _simulate(events)
    assert result["entered_trades"] == 2
    assert result["trades"][0]["exit_timestamp"] == STEP


def test_costs_are_deducted_and_drawdown_is_realised_balance_only():
    result = _simulate(
        [_event("BTC/USDT", 0, return_pct=1.0)],
        fee_bps_per_side=6.0,
        slippage_bps_per_side=2.0,
    )
    assert result["average_return_r"] == pytest.approx(0.84)
    assert result["final_balance"] == pytest.approx(302.52)
    assert result["realised_balance_max_drawdown_pct"] == 0.0

    stopped = _simulate(
        [_event("BTC/USDT", 0, stop_hit=True, bars_to_stop=1)],
        fee_bps_per_side=6.0,
        slippage_bps_per_side=2.0,
    )
    assert stopped["average_return_r"] == pytest.approx(-1.16)
    assert stopped["final_balance"] == pytest.approx(296.52)
    assert stopped["realised_balance_max_drawdown_pct"] == pytest.approx(1.16)


def test_one_and_one_point_five_percent_stops_are_separate_candidates():
    event = _event("BTC/USDT", 0, return_pct=1.5)
    stop_100 = _simulate([event], stop_loss_bps=100)
    stop_150 = _simulate([event], stop_loss_bps=150)
    assert stop_100["trades"][0]["notional"] == pytest.approx(300.0)
    assert stop_150["trades"][0]["notional"] == pytest.approx(200.0)
    assert stop_100["final_balance"] == pytest.approx(304.5)
    assert stop_150["final_balance"] == pytest.approx(303.0)


def test_report_keeps_holdouts_independent_and_also_builds_combined_curve():
    events = [
        _event("BTC/USDT", 0, split="DEVELOPMENT"),
        _event("ETH/USDT", 20 * STEP, split="HOLDOUT_A"),
        _event("SOL/USDT", 40 * STEP, split="HOLDOUT_B"),
    ]
    report = portfolio.build_portfolio_report(
        [{"manifest": {}}],
        events,
        fee_bps_per_side=0.0,
        slippage_bps_per_side=0.0,
    )
    assert len(report["simulations"]) == 8
    rows = {(row["scope"], row["stop_loss_bps"]): row for row in report["simulations"]}
    assert rows[("HOLDOUT_A", 100)]["starting_balance"] == 300.0
    assert rows[("HOLDOUT_A", 100)]["final_balance"] == pytest.approx(303.0)
    assert rows[("COMBINED", 100)]["final_balance"] == pytest.approx(309.0903)


def test_invalid_duplicate_event_fails_closed():
    event = _event("BTC/USDT", 0)
    with pytest.raises(ValueError, match="DUPLICATE_EVENT"):
        _simulate([event, event])
