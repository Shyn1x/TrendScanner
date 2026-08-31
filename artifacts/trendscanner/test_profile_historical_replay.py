from __future__ import annotations

import copy

import historical_replay
from profile_historical_replay import (
    build_timing_buckets,
    run_profiling_session,
    wrap_analyze_timeframe,
)


def test_wrap_records_call_count_and_elapsed() -> None:
    def fake_analyze(historical_df):
        return {"trend": "OK"}

    wrapper, records = wrap_analyze_timeframe(fake_analyze)
    wrapper([1, 2, 3])
    wrapper([1, 2, 3, 4])

    assert len(records) == 2
    assert records[0]["df_len"] == 3
    assert records[1]["df_len"] == 4
    assert all(rec["elapsed"] >= 0 for rec in records)


def test_wrap_preserves_original_return_value() -> None:
    def fake_analyze(historical_df):
        return {"trend": "LONG", "length": len(historical_df)}

    wrapper, _records = wrap_analyze_timeframe(fake_analyze)
    result = wrapper([1, 2, 3])

    assert result == {"trend": "LONG", "length": 3}


def test_build_timing_buckets_splits_by_bucket_size() -> None:
    records = [{"df_len": i, "elapsed": 0.01} for i in range(250)]
    buckets = build_timing_buckets(records, bucket_size=100)

    assert len(buckets) == 3
    assert buckets[0]["call_count"] == 100
    assert buckets[0]["step_range"] == "0-99"
    assert buckets[1]["call_count"] == 100
    assert buckets[1]["step_range"] == "100-199"
    assert buckets[2]["call_count"] == 50
    assert buckets[2]["step_range"] == "200-249"


def test_build_timing_buckets_empty() -> None:
    assert build_timing_buckets([]) == []


def test_run_profiling_session_restores_analyze_timeframe() -> None:
    original = historical_replay.analyze_timeframe

    def fake_get_data(symbol: str, timeframe: str, total_limit: int) -> list[int]:
        return [0] * 5

    def fake_replay(df, *, symbol, timeframe, warmup_bars, max_replay_bars) -> dict:
        for i in range(3):
            historical_replay.analyze_timeframe([1] * (i + 1))
        return {"replay_results": [], "meta": {"replay_count": 3}}

    result = run_profiling_session(get_data_fn=fake_get_data, replay_timeframe_fn=fake_replay)

    assert historical_replay.analyze_timeframe is original
    assert result["analyze_timeframe_call_count"] == 3
    assert len(result["timing_buckets"]) == 1
    assert result["timing_buckets"][0]["call_count"] == 3


def test_run_profiling_session_restores_analyze_timeframe_even_on_error() -> None:
    original = historical_replay.analyze_timeframe

    def fake_get_data(symbol: str, timeframe: str, total_limit: int) -> list[int]:
        return [0] * 5

    def fake_replay(df, **kwargs) -> dict:
        raise RuntimeError("boom")

    raised = False
    try:
        run_profiling_session(get_data_fn=fake_get_data, replay_timeframe_fn=fake_replay)
    except RuntimeError:
        raised = True

    assert raised
    assert historical_replay.analyze_timeframe is original


def test_run_profiling_session_does_not_mutate_input_df() -> None:
    def fake_get_data(symbol: str, timeframe: str, total_limit: int) -> list[int]:
        return [1, 2, 3]

    seen_df: dict[str, list[int]] = {}

    def fake_replay(df, *, symbol, timeframe, warmup_bars, max_replay_bars) -> dict:
        seen_df["df"] = copy.deepcopy(df)
        historical_replay.analyze_timeframe(df)
        return {"replay_results": [], "meta": {}}

    run_profiling_session(get_data_fn=fake_get_data, replay_timeframe_fn=fake_replay)

    assert seen_df["df"] == [1, 2, 3]


def test_run_profiling_session_no_network_required() -> None:
    calls = {"n": 0}

    def fake_get_data(symbol: str, timeframe: str, total_limit: int) -> list[int]:
        calls["n"] += 1
        return [0] * 2

    def fake_replay(df, **kwargs) -> dict:
        historical_replay.analyze_timeframe(df)
        return {"replay_results": [], "meta": {}}

    run_profiling_session(get_data_fn=fake_get_data, replay_timeframe_fn=fake_replay)

    assert calls["n"] == 1


if __name__ == "__main__":
    tests = [
        test_wrap_records_call_count_and_elapsed,
        test_wrap_preserves_original_return_value,
        test_build_timing_buckets_splits_by_bucket_size,
        test_build_timing_buckets_empty,
        test_run_profiling_session_restores_analyze_timeframe,
        test_run_profiling_session_restores_analyze_timeframe_even_on_error,
        test_run_profiling_session_does_not_mutate_input_df,
        test_run_profiling_session_no_network_required,
    ]

    for test in tests:
        test()

    print(f"{len(tests)} profile historical replay tests passed.")
