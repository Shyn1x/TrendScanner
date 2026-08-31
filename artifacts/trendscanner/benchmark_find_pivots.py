"""Microbenchmark the reference and optimized find_pivots implementations."""

import statistics
import time

from research_data import get_research_data
from trendlines import find_pivots

REPEATS = 25


def reference_find_pivots(df, window=5):
    highs = []
    lows = []

    for i in range(window, len(df) - window):
        high = df.high.iloc[i]
        low = df.low.iloc[i]

        if high == max(df.high.iloc[i - window:i + window]):
            highs.append((i, high))

        if low == min(df.low.iloc[i - window:i + window]):
            lows.append((i, low))

    return highs, lows


def measure(function, df):
    durations = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        function(df)
        durations.append(time.perf_counter() - started)
    return statistics.mean(durations), statistics.median(durations)


def main():
    df = get_research_data("BTC/USDT", "4h", 1000)
    if len(df) < 1000:
        raise RuntimeError(f"Expected 1000 BTC 4h candles, received {len(df)}")
    df = df.iloc[-1000:].reset_index(drop=True)

    reference_result = reference_find_pivots(df)
    optimized_result = find_pivots(df)
    if optimized_result != reference_result:
        raise AssertionError("Optimized output differs from reference output")

    reference_mean, reference_median = measure(reference_find_pivots, df)
    optimized_mean, optimized_median = measure(find_pivots, df)

    print("find_pivots benchmark: BTC/USDT 4h, 1000 candles")
    print(f"repeats: {REPEATS}")
    print(f"reference mean: {reference_mean * 1000:.3f} ms")
    print(f"reference median: {reference_median * 1000:.3f} ms")
    print(f"optimized mean: {optimized_mean * 1000:.3f} ms")
    print(f"optimized median: {optimized_median * 1000:.3f} ms")
    print(f"mean speedup: {reference_mean / optimized_mean:.2f}x")
    print(f"median speedup: {reference_median / optimized_median:.2f}x")


if __name__ == "__main__":
    main()