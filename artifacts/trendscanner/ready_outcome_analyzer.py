from __future__ import annotations

import math
import statistics
from typing import Any

from ready_engine import evaluate_ready_candidate


VALID_DIRECTIONS = ("LONG", "SHORT")


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _normalize_horizons(horizons: tuple[int, ...]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for item in horizons:
        if isinstance(item, bool):
            continue
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _build_time_index_map(df: Any) -> dict[int, list[int]]:
    mapping: dict[int, list[int]] = {}
    if df is None or "time" not in getattr(df, "columns", []):
        return mapping

    time_values = df["time"]
    for pos, raw_ts in enumerate(time_values):
        ts = _safe_int(raw_ts)
        if ts is None:
            continue
        mapping.setdefault(ts, []).append(pos)

    return mapping


def _empty_horizon_payload() -> dict[str, Any]:
    return {
        "available": False,
        "mfe_pct": None,
        "mae_pct": None,
        "close_return_pct": None,
        "bars_to_mfe": None,
        "bars_to_mae": None,
    }


def _first_index_of_target(values: list[float], target: float) -> int | None:
    for idx, value in enumerate(values, start=1):
        if math.isclose(value, target, rel_tol=0.0, abs_tol=0.0):
            return idx
    return None


def _compute_horizon_metrics(
    direction: str,
    entry_price: float,
    future_highs: list[float],
    future_lows: list[float],
    future_closes: list[float],
) -> dict[str, Any]:
    if not future_highs or not future_lows or not future_closes:
        return _empty_horizon_payload()

    max_high = max(future_highs)
    min_low = min(future_lows)
    last_close = future_closes[-1]

    if direction == "LONG":
        mfe_pct = (max_high - entry_price) / entry_price * 100.0
        mae_pct = (entry_price - min_low) / entry_price * 100.0
        close_return_pct = (last_close - entry_price) / entry_price * 100.0
        bars_to_mfe = _first_index_of_target(future_highs, max_high)
        bars_to_mae = _first_index_of_target(future_lows, min_low)
    else:
        mfe_pct = (entry_price - min_low) / entry_price * 100.0
        mae_pct = (max_high - entry_price) / entry_price * 100.0
        close_return_pct = (entry_price - last_close) / entry_price * 100.0
        bars_to_mfe = _first_index_of_target(future_lows, min_low)
        bars_to_mae = _first_index_of_target(future_highs, max_high)

    return {
        "available": True,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "close_return_pct": close_return_pct,
        "bars_to_mfe": bars_to_mfe,
        "bars_to_mae": bars_to_mae,
    }


def _collect_future_values(window: Any) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []

    for _, row in window.iterrows():
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        if high is None or low is None or close is None:
            return [], [], []
        highs.append(high)
        lows.append(low)
        closes.append(close)

    return highs, lows, closes


def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return statistics.mean(values), statistics.median(values)


def _rate(values: list[float], predicate) -> float | None:
    if not values:
        return None
    hits = sum(1 for value in values if predicate(value))
    return hits / len(values) * 100.0


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, str, str, str]:
    timestamp = _safe_int(event.get("ready_timestamp"))
    replay_index = _safe_int(event.get("ready_replay_index")) or 0
    return (
        timestamp if timestamp is not None else 2**63 - 1,
        replay_index,
        str(event.get("symbol", "")),
        str(event.get("timeframe", "")),
        str(event.get("direction", "")),
    )


def analyze_ready_outcomes(
    df,
    replay_results: list,
    horizons: tuple[int, ...] = (3, 5, 10),
) -> dict:
    """
    Analyze historical price outcomes after unique READY transitions.

    Unique event for each symbol+timeframe+direction:
    transition ready False -> True based on evaluate_ready_candidate().
    """
    horizon_values = _normalize_horizons(horizons)
    horizon_keys = [str(item) for item in horizon_values]

    diagnostics = {
        "timestamp_not_found": 0,
        "invalid_price": 0,
        "malformed_replay_entry": 0,
    }

    events: list[dict[str, Any]] = []
    skipped_events = 0

    time_index_map = _build_time_index_map(df)

    ready_state: dict[tuple[str, str, str], bool] = {}

    replay_sequence = replay_results if isinstance(replay_results, list) else []

    for raw_entry in replay_sequence:
        if not isinstance(raw_entry, dict):
            diagnostics["malformed_replay_entry"] += 1
            skipped_events += 1
            continue

        symbol = str(raw_entry.get("symbol", "")).strip()
        timeframe = str(raw_entry.get("timeframe", "")).strip()
        replay_index = _safe_int(raw_entry.get("replay_index"))
        signal_timestamp = _safe_int(raw_entry.get("signal_timestamp"))
        timeframe_result = raw_entry.get("timeframe_result")

        if not symbol or not timeframe or replay_index is None or not isinstance(timeframe_result, dict):
            diagnostics["malformed_replay_entry"] += 1
            skipped_events += 1
            continue

        for direction in VALID_DIRECTIONS:
            ready_eval = evaluate_ready_candidate(
                symbol,
                timeframe,
                direction,
                timeframe_result,
            )
            is_ready = bool(ready_eval.get("ready"))
            state_key = (symbol, timeframe, direction)
            was_ready = ready_state.get(state_key, False)
            ready_state[state_key] = is_ready

            if not is_ready or was_ready:
                continue

            mapped_indices = time_index_map.get(signal_timestamp) if signal_timestamp is not None else None
            if not mapped_indices or len(mapped_indices) != 1:
                diagnostics["timestamp_not_found"] += 1
                skipped_events += 1
                continue

            candle_index = mapped_indices[0]
            entry_reference_price = _safe_float(df.iloc[candle_index].get("close"))
            if entry_reference_price is None or entry_reference_price <= 0.0:
                diagnostics["invalid_price"] += 1
                skipped_events += 1
                continue

            event_horizons: dict[str, dict[str, Any]] = {}
            for horizon in horizon_values:
                start = candle_index + 1
                stop = start + horizon
                key = str(horizon)

                if df is None or stop > len(df):
                    event_horizons[key] = _empty_horizon_payload()
                    continue

                window = df.iloc[start:stop]
                future_highs, future_lows, future_closes = _collect_future_values(window)
                if not future_highs:
                    event_horizons[key] = _empty_horizon_payload()
                    continue

                event_horizons[key] = _compute_horizon_metrics(
                    direction,
                    entry_reference_price,
                    future_highs,
                    future_lows,
                    future_closes,
                )

            warnings = ready_eval.get("warnings")
            event = {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "ready_replay_index": replay_index,
                "ready_timestamp": signal_timestamp,
                "ready_candle_index": candle_index,
                "entry_reference_price": entry_reference_price,
                "ready_confidence": _safe_float(ready_eval.get("confidence")),
                "ready_decision_score": _safe_float(ready_eval.get("decision_score")),
                "ready_warnings": list(warnings) if isinstance(warnings, list) else [],
                "horizons": event_horizons,
            }
            events.append(event)

    events.sort(key=_event_sort_key)

    by_direction = {"LONG": 0, "SHORT": 0}
    for event in events:
        direction = str(event.get("direction", "")).upper()
        if direction in by_direction:
            by_direction[direction] += 1

    by_horizon: dict[str, dict[str, Any]] = {}
    for horizon_key in horizon_keys:
        available_events = 0
        mfe_values: list[float] = []
        mae_values: list[float] = []
        close_values: list[float] = []

        for event in events:
            horizon_data = event.get("horizons", {}).get(horizon_key, {})
            if not isinstance(horizon_data, dict):
                continue
            if not bool(horizon_data.get("available")):
                continue

            available_events += 1
            mfe = _safe_float(horizon_data.get("mfe_pct"))
            mae = _safe_float(horizon_data.get("mae_pct"))
            close_ret = _safe_float(horizon_data.get("close_return_pct"))

            if mfe is not None:
                mfe_values.append(mfe)
            if mae is not None:
                mae_values.append(mae)
            if close_ret is not None:
                close_values.append(close_ret)

        avg_mfe, med_mfe = _stats(mfe_values)
        avg_mae, med_mae = _stats(mae_values)
        avg_close, _ = _stats(close_values)

        by_horizon[horizon_key] = {
            "available_events": available_events,
            "average_mfe_pct": avg_mfe,
            "median_mfe_pct": med_mfe,
            "average_mae_pct": avg_mae,
            "median_mae_pct": med_mae,
            "average_close_return_pct": avg_close,
            "positive_close_rate": _rate(close_values, lambda value: value > 0.0),
            "mfe_ge_1_pct_rate": _rate(mfe_values, lambda value: value >= 1.0),
            "mfe_ge_2_pct_rate": _rate(mfe_values, lambda value: value >= 2.0),
            "mfe_ge_3_pct_rate": _rate(mfe_values, lambda value: value >= 3.0),
        }

    summary = {
        "ready_events": len(events),
        "skipped_events": skipped_events,
        "by_direction": by_direction,
        "by_horizon": by_horizon,
        "diagnostics": diagnostics,
    }

    return {
        "events": events,
        "summary": summary,
    }
