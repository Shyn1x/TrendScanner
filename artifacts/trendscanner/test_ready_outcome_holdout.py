from __future__ import annotations

import copy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from ready_outcome_holdout import (
    HOLDOUT_LIMIT,
    build_holdout_comparison,
    classify_hypothesis,
    derive_current_first_timestamp,
    prepare_holdout_window,
    run_ready_outcome_holdout,
)


TF = 4 * 60 * 60 * 1000


def _event(symbol="BTC/USDT", direction="LONG", timestamp=1_000 * TF, index=10, mfe=2.0, mae=1.0):
    return {"symbol": symbol, "timeframe": "4h", "direction": direction, "ready_timestamp": timestamp, "ready_candle_index": index, "horizons": {"5": {"available": True, "mfe_pct": mfe, "mae_pct": mae, "close_return_pct": 0.5}}}


def _payload(events):
    return {"runs": [{"status": "OK", "events": events}]}


def test_derives_exact_boundary_and_rejects_ambiguity():
    boundary = 900 * TF
    payload = _payload([_event(timestamp=910 * TF, index=10), _event(timestamp=920 * TF, index=20)])
    assert derive_current_first_timestamp(payload, "BTC/USDT", "4h") == boundary
    payload["runs"][0]["events"].append(_event(timestamp=930 * TF, index=10))
    try:
        derive_current_first_timestamp(payload, "BTC/USDT", "4h")
        assert False
    except ValueError as exc:
        assert "ambiguous" in str(exc)


def test_prepare_window_has_no_overlap():
    frame = pd.DataFrame({"time": [100, 200, 300], "open": [1, 1, 1], "high": [1, 1, 1], "low": [1, 1, 1], "close": [1, 1, 1], "volume": [1, 1, 1]})
    _, diagnostic = prepare_holdout_window(frame, 400)
    assert diagnostic["overlap_count"] == 0
    assert diagnostic["holdout_last_timestamp"] < diagnostic["derived_current_first_timestamp"]


def test_long_short_aggregation_and_classification():
    runs = [{"status": "OK", "events": [_event(symbol="A", direction="LONG", mfe=3, mae=1) for _ in range(5)] + [_event(symbol="A", direction="SHORT", mfe=1, mae=2) for _ in range(5)] + [_event(symbol="B", direction="LONG", mfe=3, mae=1) for _ in range(5)] + [_event(symbol="B", direction="SHORT", mfe=1, mae=2) for _ in range(5)]}]
    comparison = build_holdout_comparison(runs)
    assert comparison["aggregate"]["LONG"]["mfe_minus_mae_pct"] == 2
    assert comparison["aggregate"]["SHORT"]["mfe_minus_mae_pct"] == -1
    assert comparison["hypothesis_result"] == "SUPPORTED"


def test_hypothesis_mixed_and_not_supported():
    mixed = {"aggregate": {"LONG": {"mfe_minus_mae_pct": 1}, "SHORT": {"mfe_minus_mae_pct": 0}}, "per_symbol": {"median_long_mfe_minus_mae_pct": 1, "comparable_symbols": 1, "long_greater_than_short_symbols": 0}}
    rejected = {"aggregate": {"LONG": {"mfe_minus_mae_pct": 0}, "SHORT": {"mfe_minus_mae_pct": 1}}, "per_symbol": {"median_long_mfe_minus_mae_pct": 1, "comparable_symbols": 1, "long_greater_than_short_symbols": 0}}
    assert classify_hypothesis(mixed) == "MIXED"
    assert classify_hypothesis(rejected) == "NOT_SUPPORTED"


def test_runner_uses_limit_does_not_overwrite_current_and_does_not_mutate_input():
    source = _payload([_event()])
    original = copy.deepcopy(source)
    calls = []
    with TemporaryDirectory() as tmp:
        current_path = Path(tmp) / "ready_outcome_results.json"
        current_path.write_text("current", encoding="utf-8")
        output_json, output_csv = Path(tmp) / "holdout.json", Path(tmp) / "holdout.csv"

        def loader(symbol, timeframe, before_timestamp, total_limit):
            calls.append(total_limit)
            return pd.DataFrame({"time": list(range(before_timestamp - HOLDOUT_LIMIT, before_timestamp)), "open": [1] * HOLDOUT_LIMIT, "high": [1] * HOLDOUT_LIMIT, "low": [1] * HOLDOUT_LIMIT, "close": [1] * HOLDOUT_LIMIT, "volume": [1] * HOLDOUT_LIMIT})

        payload = run_ready_outcome_holdout(current_payload=source, symbols=["BTC/USDT"], output_json=str(output_json), output_csv=str(output_csv), get_data_before_fn=loader, replay_timeframe_fn=lambda *args, **kwargs: {"meta": {}, "replay_results": []}, analyze_ready_outcomes_fn=lambda *args, **kwargs: {"summary": {"ready_events": 0, "skipped_events": 0}, "events": []}, progress=False)
        assert calls == [HOLDOUT_LIMIT]
        assert payload["runs"][0]["status"] == "OK"
        assert current_path.read_text(encoding="utf-8") == "current"
        assert output_json.exists() and output_csv.exists()
    assert source == original


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)} ready outcome holdout tests passed.")