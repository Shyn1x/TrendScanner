"""Read-only profiling for historical_replay's analyze_timeframe bottleneck.

This is a research/profiling tool only. It does not modify analysis.py,
ready_engine.py, decision_engine.py, historical_replay.py, quality
calculations, thresholds, or the research dataset. The only "instrumentation"
is a monkeypatch of historical_replay.analyze_timeframe with a timing
wrapper around the *original* function, scoped to this script's own call and
restored immediately afterwards (see run_profiling_session()).

No optimizations are applied here — this only measures where the time goes.
"""

from __future__ import annotations

import cProfile
import io
import pstats
import time
from typing import Any, Callable

import historical_replay
from historical_replay import replay_timeframe
from research_data import get_research_data

SYMBOL = "BTC/USDT"
TIMEFRAME = "4h"
WARMUP_BARS = 120
MAX_REPLAY_BARS = 880
RESEARCH_DATA_LIMIT = 1000
BUCKET_SIZE = 100
TOP_CUMULATIVE_COUNT = 30
TOP_TOTAL_TIME_COUNT = 20


def wrap_analyze_timeframe(
    original: Callable[..., Any],
) -> tuple[Callable[..., Any], list[dict[str, Any]]]:
    """Wrap analyze_timeframe with per-call timing, without touching analysis.py."""
    call_records: list[dict[str, Any]] = []

    def wrapper(historical_df, *args, **kwargs):
        df_len = len(historical_df) if hasattr(historical_df, "__len__") else None
        started = time.perf_counter()
        result = original(historical_df, *args, **kwargs)
        elapsed = time.perf_counter() - started
        call_records.append({"df_len": df_len, "elapsed": elapsed})
        return result

    return wrapper, call_records


def build_timing_buckets(
    call_records: list[dict[str, Any]],
    bucket_size: int = BUCKET_SIZE,
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    total_calls = len(call_records)
    if total_calls == 0:
        return buckets

    start = 0
    while start < total_calls:
        end = min(start + bucket_size, total_calls)
        chunk = call_records[start:end]
        elapsed_values = [rec["elapsed"] for rec in chunk]
        df_len_values = [rec["df_len"] for rec in chunk if rec["df_len"] is not None]

        buckets.append(
            {
                "step_range": f"{start}-{end - 1}",
                "call_count": len(chunk),
                "total_time_s": sum(elapsed_values),
                "avg_time_s": sum(elapsed_values) / len(elapsed_values) if elapsed_values else None,
                "min_time_s": min(elapsed_values) if elapsed_values else None,
                "max_time_s": max(elapsed_values) if elapsed_values else None,
                "min_df_len": min(df_len_values) if df_len_values else None,
                "max_df_len": max(df_len_values) if df_len_values else None,
            }
        )
        start = end

    return buckets


def format_top_functions(profiler: cProfile.Profile, sort_by: str, count: int) -> str:
    buffer = io.StringIO()
    stats = pstats.Stats(profiler, stream=buffer)
    stats.strip_dirs()
    stats.sort_stats(sort_by)
    stats.print_stats(count)
    return buffer.getvalue()


def run_profiling_session(
    *,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
    warmup_bars: int = WARMUP_BARS,
    max_replay_bars: int = MAX_REPLAY_BARS,
    research_data_limit: int = RESEARCH_DATA_LIMIT,
    get_data_fn: Callable[[str, str, int], Any] = get_research_data,
    replay_timeframe_fn: Callable[..., dict[str, Any]] = replay_timeframe,
) -> dict[str, Any]:
    df = get_data_fn(symbol, timeframe, research_data_limit)
    rows_loaded = len(df) if hasattr(df, "__len__") else None

    original_analyze_timeframe = historical_replay.analyze_timeframe
    wrapper, call_records = wrap_analyze_timeframe(original_analyze_timeframe)

    profiler = cProfile.Profile()

    historical_replay.analyze_timeframe = wrapper
    try:
        total_started = time.perf_counter()
        profiler.enable()
        try:
            replay = replay_timeframe_fn(
                df,
                symbol=symbol,
                timeframe=timeframe,
                warmup_bars=warmup_bars,
                max_replay_bars=max_replay_bars,
            )
        finally:
            profiler.disable()
        total_elapsed = time.perf_counter() - total_started
    finally:
        historical_replay.analyze_timeframe = original_analyze_timeframe

    buckets = build_timing_buckets(call_records)
    elapsed_values = [rec["elapsed"] for rec in call_records]
    call_count = len(call_records)

    return {
        "rows_loaded": rows_loaded,
        "total_replay_runtime_s": total_elapsed,
        "analyze_timeframe_call_count": call_count,
        "analyze_timeframe_total_time_s": sum(elapsed_values) if elapsed_values else None,
        "analyze_timeframe_avg_time_s": (sum(elapsed_values) / call_count) if call_count else None,
        "analyze_timeframe_min_time_s": min(elapsed_values) if elapsed_values else None,
        "analyze_timeframe_max_time_s": max(elapsed_values) if elapsed_values else None,
        "timing_buckets": buckets,
        "replay_meta": replay.get("meta", {}) if isinstance(replay, dict) else {},
        "profiler": profiler,
        "call_records": call_records,
    }


def main() -> None:
    session = run_profiling_session()

    print("Historical Replay Profiling (BTC/USDT 4h, research-only, no logic changes)")
    print("=" * 72)
    print(f"rows_loaded={session['rows_loaded']}")
    print(f"total_replay_runtime_s={session['total_replay_runtime_s']:.2f}")
    print(f"analyze_timeframe_call_count={session['analyze_timeframe_call_count']}")

    avg = session["analyze_timeframe_avg_time_s"]
    lo = session["analyze_timeframe_min_time_s"]
    hi = session["analyze_timeframe_max_time_s"]
    total = session["analyze_timeframe_total_time_s"]
    print(
        f"analyze_timeframe avg={avg:.4f}s min={lo:.4f}s max={hi:.4f}s total={total:.2f}s"
        if avg is not None
        else "analyze_timeframe: no calls recorded"
    )

    print("-" * 72)
    print("Timing buckets (by replay step order, chunks of 100 steps)")
    for bucket in session["timing_buckets"]:
        print(
            f"steps {bucket['step_range']} | N={bucket['call_count']} "
            f"| total={bucket['total_time_s']:.2f}s | avg={bucket['avg_time_s']:.4f}s "
            f"| min={bucket['min_time_s']:.4f}s | max={bucket['max_time_s']:.4f}s "
            f"| df_len {bucket['min_df_len']}-{bucket['max_df_len']}"
        )

    print("-" * 72)
    print(f"Top {TOP_CUMULATIVE_COUNT} functions by cumulative time")
    print(format_top_functions(session["profiler"], "cumulative", TOP_CUMULATIVE_COUNT))

    print(f"Top {TOP_TOTAL_TIME_COUNT} functions by total (tottime)")
    print(format_top_functions(session["profiler"], "tottime", TOP_TOTAL_TIME_COUNT))


if __name__ == "__main__":
    main()
