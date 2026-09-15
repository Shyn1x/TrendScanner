"""Analyze realized outcomes for immutable 1h/15m prospective READY events.

This is research-only. It never changes READY state/events or production decisions.
A derived outcome row is written only after the full requested horizon has closed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter

import pandas as pd

import lower_tf_storage
from lower_tf_shadow import EXPERIMENT_VERSION, TIMEFRAME_MS
from research_data import get_research_data_before
from scanner import exchange

HORIZONS = (1, 3, 6, 12)
OUTCOME_VERSION = "lower-tf-outcome-v1-bars-1-3-6-12"
OUTCOMES_TABLE = "lower_tf_prospective_outcomes"
MAX_NO_TICK_FILLS = 5
OHLCV_COLUMNS = ["time", "open", "high", "low", "close", "volume"]
VALID_DIRECTIONS = ("LONG", "SHORT")


def _safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_extreme_index(values, target):
    for index, value in enumerate(values, start=1):
        if value == target:
            return index
    return None


def _compute_metrics(direction, entry_price, future_rows):
    if direction not in VALID_DIRECTIONS:
        raise ValueError("INVALID_DIRECTION")
    entry = _safe_float(entry_price)
    if entry is None or entry <= 0:
        raise ValueError("INVALID_ENTRY_PRICE")
    if future_rows is None or future_rows.empty:
        raise ValueError("EMPTY_FUTURE_WINDOW")

    highs = [_safe_float(value) for value in future_rows["high"]]
    lows = [_safe_float(value) for value in future_rows["low"]]
    closes = [_safe_float(value) for value in future_rows["close"]]
    if any(value is None for value in highs + lows + closes):
        raise ValueError("INVALID_FUTURE_OHLC")

    max_high = max(highs)
    min_low = min(lows)
    final_close = closes[-1]

    if direction == "LONG":
        mfe = max(0.0, (max_high - entry) / entry * 100.0)
        mae = max(0.0, (entry - min_low) / entry * 100.0)
        close_return = (final_close - entry) / entry * 100.0
        bars_to_mfe = _first_extreme_index(highs, max_high) if mfe > 0 else None
        bars_to_mae = _first_extreme_index(lows, min_low) if mae > 0 else None
    else:
        mfe = max(0.0, (entry - min_low) / entry * 100.0)
        mae = max(0.0, (max_high - entry) / entry * 100.0)
        close_return = (entry - final_close) / entry * 100.0
        bars_to_mfe = _first_extreme_index(lows, min_low) if mfe > 0 else None
        bars_to_mae = _first_extreme_index(highs, max_high) if mae > 0 else None

    return {
        "mfe_pct": mfe,
        "mae_pct": mae,
        "close_return_pct": close_return,
        "bars_to_mfe": bars_to_mfe,
        "bars_to_mae": bars_to_mae,
    }


def _build_exact_window(source, timeframe, ready_timestamp, horizon):
    """Return signal candle + exactly `horizon` future slots.

    Missing no-tick slots use the same bounded flat previous-close policy as the
    lower-TF prospective collector. More than five missing slots fails closed.
    """
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("INVALID_TIMEFRAME")
    if horizon not in HORIZONS:
        raise ValueError("INVALID_HORIZON")
    if source is None or source.empty or "time" not in source.columns:
        raise ValueError("EMPTY_SOURCE")

    tf_ms = TIMEFRAME_MS[timeframe]
    ready = int(ready_timestamp)
    clean = source[OHLCV_COLUMNS].copy()
    clean["time"] = clean["time"].map(int)
    clean = clean.drop_duplicates(subset="time", keep="last").sort_values("time")

    expected_times = [ready + index * tf_ms for index in range(horizon + 1)]
    last_expected = expected_times[-1]
    rows_by_time = {
        int(row["time"]): [int(row["time"]), *[row[column] for column in OHLCV_COLUMNS[1:]]]
        for _, row in clean.iterrows()
        if ready <= int(row["time"]) <= last_expected
    }
    missing = [timestamp for timestamp in expected_times if timestamp not in rows_by_time]
    if len(missing) > MAX_NO_TICK_FILLS:
        raise ValueError("TOO_MANY_NO_TICK_SLOTS")

    prior = clean[clean["time"] < ready]
    previous_close = None if prior.empty else _safe_float(prior.iloc[-1]["close"])
    repaired_rows = []
    filled = []

    for timestamp in expected_times:
        row = rows_by_time.get(timestamp)
        if row is None:
            if previous_close is None:
                raise ValueError("MISSING_PREVIOUS_CLOSE")
            row = [timestamp, previous_close, previous_close, previous_close, previous_close, 0.0]
            filled.append(timestamp)
        close_value = _safe_float(row[4])
        if close_value is None or close_value <= 0:
            raise ValueError("INVALID_CLOSE")
        repaired_rows.append(row)
        previous_close = close_value

    frame = pd.DataFrame(repaired_rows, columns=OHLCV_COLUMNS)
    frame.attrs["filled_no_tick_timestamps"] = filled
    return frame


def _mature_horizons(ready_timestamp, timeframe, now_ms):
    tf_ms = TIMEFRAME_MS[timeframe]
    ready = int(ready_timestamp)
    now = int(now_ms)
    return [h for h in HORIZONS if ready + (h + 1) * tf_ms <= now]


def _parse_event(payload):
    event = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(event, dict):
        raise ValueError("INVALID_EVENT_PAYLOAD")
    if event.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("WRONG_EXPERIMENT_VERSION")
    symbol = event.get("symbol")
    timeframe = event.get("timeframe")
    direction = event.get("direction")
    ready_timestamp = event.get("ready_timestamp")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("INVALID_SYMBOL")
    if timeframe not in TIMEFRAME_MS:
        raise ValueError("INVALID_TIMEFRAME")
    if direction not in VALID_DIRECTIONS:
        raise ValueError("INVALID_DIRECTION")
    if isinstance(ready_timestamp, bool):
        raise ValueError("INVALID_READY_TIMESTAMP")
    ready_timestamp = int(ready_timestamp)
    if ready_timestamp < 0 or ready_timestamp % TIMEFRAME_MS[timeframe]:
        raise ValueError("INVALID_READY_TIMESTAMP")
    return event


def _bootstrap_outcomes(cur):
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {OUTCOMES_TABLE} ("
        "outcome_version TEXT NOT NULL, experiment_version TEXT NOT NULL, "
        "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, direction TEXT NOT NULL, "
        "ready_timestamp BIGINT NOT NULL, horizon_bars INTEGER NOT NULL, "
        "target_4h_timestamp BIGINT, entry_reference_price DOUBLE PRECISION NOT NULL, "
        "mfe_pct DOUBLE PRECISION NOT NULL, mae_pct DOUBLE PRECISION NOT NULL, "
        "close_return_pct DOUBLE PRECISION NOT NULL, bars_to_mfe INTEGER, bars_to_mae INTEGER, "
        "no_tick_fills INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "PRIMARY KEY (outcome_version, experiment_version, symbol, timeframe, direction, ready_timestamp, horizon_bars))"
    )


def _load_events(cur):
    cur.execute(
        f"SELECT payload FROM {lower_tf_storage.EVENTS_TABLE} "
        "WHERE experiment_version=%s ORDER BY ready_timestamp, symbol, timeframe, direction",
        (EXPERIMENT_VERSION,),
    )
    return [row[0] for row in cur.fetchall()]


def _load_existing_keys(cur):
    cur.execute("SELECT to_regclass(%s)", (OUTCOMES_TABLE,))
    if cur.fetchone()[0] is None:
        return set()
    cur.execute(
        f"SELECT symbol, timeframe, direction, ready_timestamp, horizon_bars "
        f"FROM {OUTCOMES_TABLE} WHERE outcome_version=%s AND experiment_version=%s",
        (OUTCOME_VERSION, EXPERIMENT_VERSION),
    )
    return {(row[0], row[1], row[2], int(row[3]), int(row[4])) for row in cur.fetchall()}


def _insert_outcome(cur, row):
    cur.execute(
        f"INSERT INTO {OUTCOMES_TABLE} ("
        "outcome_version, experiment_version, symbol, timeframe, direction, ready_timestamp, "
        "horizon_bars, target_4h_timestamp, entry_reference_price, mfe_pct, mae_pct, "
        "close_return_pct, bars_to_mfe, bars_to_mae, no_tick_fills) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING",
        (
            OUTCOME_VERSION,
            EXPERIMENT_VERSION,
            row["symbol"],
            row["timeframe"],
            row["direction"],
            row["ready_timestamp"],
            row["horizon_bars"],
            row["target_4h_timestamp"],
            row["entry_reference_price"],
            row["mfe_pct"],
            row["mae_pct"],
            row["close_return_pct"],
            row["bars_to_mfe"],
            row["bars_to_mae"],
            row["no_tick_fills"],
        ),
    )
    return cur.rowcount == 1


def _print_summary(cur):
    cur.execute(
        f"SELECT timeframe, direction, horizon_bars, COUNT(*), "
        "AVG(mfe_pct), AVG(mae_pct), AVG(close_return_pct), "
        "AVG(CASE WHEN close_return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100.0 "
        f"FROM {OUTCOMES_TABLE} WHERE outcome_version=%s AND experiment_version=%s "
        "GROUP BY timeframe, direction, horizon_bars ORDER BY timeframe, direction, horizon_bars",
        (OUTCOME_VERSION, EXPERIMENT_VERSION),
    )
    print("Accumulated outcome summary:")
    rows = cur.fetchall()
    if not rows:
        print("  no completed outcomes yet")
        return
    for timeframe, direction, horizon, count, avg_mfe, avg_mae, avg_close, positive_rate in rows:
        print(
            f"  {timeframe} {direction} h={horizon}: N={count} "
            f"MFE={float(avg_mfe):.3f}% MAE={float(avg_mae):.3f}% "
            f"close={float(avg_close):.3f}% positive={float(positive_rate):.1f}%"
        )


def analyze(*, write=False, database_url=None, now_ms=None):
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("FAIL: DATABASE_URL is required")
    now = int(exchange.milliseconds() if now_ms is None else now_ms)

    proposed = []
    failures = []
    pending = 0
    parsed_events = []

    with lower_tf_storage._connect_postgres(url) as connection:
        with connection.cursor() as cur:
            event_payloads = _load_events(cur)
            existing = _load_existing_keys(cur)

            for payload in event_payloads:
                try:
                    parsed_events.append(_parse_event(payload))
                except Exception as exc:
                    failures.append(("event", type(exc).__name__))

            for event in parsed_events:
                symbol = event["symbol"]
                timeframe = event["timeframe"]
                direction = event["direction"]
                ready = int(event["ready_timestamp"])
                mature = _mature_horizons(ready, timeframe, now)
                missing_horizons = [
                    horizon for horizon in mature
                    if (symbol, timeframe, direction, ready, horizon) not in existing
                ]
                pending += len(HORIZONS) - len(mature)
                if not missing_horizons:
                    continue

                max_horizon = max(missing_horizons)
                tf_ms = TIMEFRAME_MS[timeframe]
                try:
                    source = get_research_data_before(
                        symbol,
                        timeframe,
                        before_timestamp=ready + (max_horizon + 1) * tf_ms,
                        total_limit=max_horizon + MAX_NO_TICK_FILLS + 4,
                    )
                    for horizon in missing_horizons:
                        window = _build_exact_window(source, timeframe, ready, horizon)
                        entry = _safe_float(window.iloc[0]["close"])
                        metrics = _compute_metrics(direction, entry, window.iloc[1:])
                        market_context = event.get("market_context")
                        target_4h = (
                            market_context.get("target_4h_timestamp")
                            if isinstance(market_context, dict)
                            else None
                        )
                        proposed.append({
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "direction": direction,
                            "ready_timestamp": ready,
                            "horizon_bars": horizon,
                            "target_4h_timestamp": int(target_4h) if target_4h is not None else None,
                            "entry_reference_price": entry,
                            **metrics,
                            "no_tick_fills": len(window.attrs.get("filled_no_tick_timestamps", [])),
                        })
                except Exception as exc:
                    failures.append((symbol, timeframe, direction, ready, type(exc).__name__))

            print(f"Outcome version: {OUTCOME_VERSION}")
            print(f"Prospective events: {len(parsed_events)}")
            print(f"Existing completed rows: {len(existing)}")
            print(f"New completed rows: {len(proposed)}")
            print(f"Still-pending horizon slots: {pending}")
            print(f"Failures: {failures}")
            print(f"New rows by timeframe: {dict(Counter(row['timeframe'] for row in proposed))}")

            if failures:
                raise SystemExit("FAIL: one or more outcome events could not be analyzed")

            if not write:
                print("DRY RUN: PASS")
                return proposed

            _bootstrap_outcomes(cur)
            inserted = sum(1 for row in proposed if _insert_outcome(cur, row))
            print(f"Inserted rows: {inserted}")
            _print_summary(cur)
        connection.commit()

    print("LOWER-TF OUTCOME WRITE: PASS")
    return proposed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    analyze(write=args.write)


if __name__ == "__main__":
    main()
