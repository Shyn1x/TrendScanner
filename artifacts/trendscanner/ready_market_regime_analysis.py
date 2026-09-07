"""Past-only market-level regime diagnostics for READY outcome periods."""
from __future__ import annotations

import copy
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ready_outcome_holdout import derive_current_first_timestamp
from research_data import get_research_data_before

CURRENT_PATH = "artifacts/trendscanner/ready_outcome_results.json"
HOLDOUT_PATH = "artifacts/trendscanner/ready_outcome_holdout_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_market_regime_report.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_market_regime_report.csv"
PERIODS, DIRECTIONS, TIMEFRAME_MS = ("CURRENT", "HOLDOUT"), ("LONG", "SHORT"), 14_400_000
# ATR14 needs 13 preceding candles before its 200-bar historical percentile.
CONTEXT_BARS, WINDOW_BARS, MIN_SAMPLE = 213, 1000, 15


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _records(payload: dict, period: str) -> tuple[list[dict], int]:
    result, skipped = [], 0
    for run in payload.get("runs", []) if isinstance(payload.get("runs"), list) else []:
        if not isinstance(run, dict) or run.get("status") != "OK": continue
        for event in run.get("events", []) if isinstance(run.get("events"), list) else []:
            h5 = event.get("horizons", {}).get("5") if isinstance(event, dict) and isinstance(event.get("horizons"), dict) else None
            if not isinstance(h5, dict) or not h5.get("available"): continue
            direction, timestamp = str(event.get("direction", "")).upper(), event.get("ready_timestamp")
            values = [_number(h5.get(key)) for key in ("mfe_pct", "mae_pct", "close_return_pct")]
            if direction not in DIRECTIONS or not isinstance(timestamp, int) or not str(event.get("symbol", "")).strip() or any(value is None for value in values):
                skipped += 1; continue
            record = dict(event); record.update(period=period, direction=direction, ready_timestamp=timestamp, mfe_pct=values[0], mae_pct=values[1], close_return_pct=values[2])
            result.append(record)
    return result, skipped


def _metrics(records: list[dict]) -> dict:
    mfe, mae, close = ([record[key] for record in records] for key in ("mfe_pct", "mae_pct", "close_return_pct"))
    avg_mfe, avg_mae = (statistics.mean(mfe) if mfe else None), (statistics.mean(mae) if mae else None)
    return {"n": len(records), "avg_mfe_pct": avg_mfe, "avg_mae_pct": avg_mae, "mfe_minus_mae_pct": avg_mfe - avg_mae if avg_mfe is not None and avg_mae is not None else None, "avg_close_return_pct": statistics.mean(close) if close else None, "positive_close_rate": sum(value > 0 for value in close) / len(close) * 100 if close else None, "mfe_ge_2_pct_rate": sum(value >= 2 for value in mfe) / len(mfe) * 100 if mfe else None, "low_sample": len(records) < MIN_SAMPLE}


def _ema(values: list[float], length: int) -> list[float | None]:
    output, current, alpha = [], None, 2 / (length + 1)
    for value in values:
        current = value if current is None else alpha * value + (1 - alpha) * current
        output.append(current)
    return output


def _frame_features(frame: pd.DataFrame) -> dict[int, dict]:
    frame = frame.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    close, high, low = frame["close"].astype(float).tolist(), frame["high"].astype(float).tolist(), frame["low"].astype(float).tolist()
    ema50, ema200 = _ema(close, 50), _ema(close, 200)
    features = {}
    for index, timestamp in enumerate(frame["time"].astype(int)):
        if index < 213: continue
        tr = [max(high[item] - low[item], abs(high[item] - close[item - 1]), abs(low[item] - close[item - 1])) for item in range(index - 13, index + 1)]
        atr_pct = statistics.mean(tr) / close[index] * 100
        past_atr = []
        for item in range(max(13, index - 200), index):
            item_tr = [max(high[pos] - low[pos], abs(high[pos] - close[pos - 1]), abs(low[pos] - close[pos - 1])) for pos in range(item - 13, item + 1)]
            past_atr.append(statistics.mean(item_tr) / close[item] * 100)
        percentile = sum(value <= atr_pct for value in past_atr) / 200 * 100
        features[timestamp] = {"close": close[index], "return_20": (close[index] / close[index - 20] - 1) * 100, "return_24": (close[index] / close[index - 24] - 1) * 100, "return_60": (close[index] / close[index - 60] - 1) * 100, "return_120": (close[index] / close[index - 120] - 1) * 100, "ema50": ema50[index], "ema200": ema200[index], "ema_distance": (ema50[index] - ema200[index]) / close[index] * 100, "ema50_slope_20": (ema50[index] - ema50[index - 20]) / close[index] * 100, "atr_pct": atr_pct, "prior_atr_observations": len(past_atr), "atr_percentile_past_200": percentile, "volatility": "LOW" if percentile <= 30 else "HIGH" if percentile >= 70 else "NORMAL"}
    return features


def classify_regime(btc: dict, breadth: dict) -> str:
    if btc["close"] > btc["ema200"] and breadth["breadth_above_ema50"] >= .60 and breadth["median_return_20"] > 0: return "BULL"
    if btc["close"] < btc["ema200"] and breadth["breadth_above_ema50"] <= .40 and breadth["median_return_20"] < 0: return "BEAR"
    return "MIXED"


def _boundaries(current: dict, holdout: dict, symbols: set[str]) -> dict[tuple[str, str], tuple[int, int]]:
    result = {}
    for symbol in symbols:
        current_first = derive_current_first_timestamp(current, symbol, "4h")
        result[("CURRENT", symbol)] = (current_first, current_first + (WINDOW_BARS - 1) * TIMEFRAME_MS)
    for run in holdout.get("runs", []):
        if not isinstance(run, dict) or run.get("status") != "OK": continue
        window, symbol = run.get("holdout_window", {}), run.get("symbol")
        if symbol in symbols and isinstance(window, dict) and isinstance(window.get("holdout_first_timestamp"), int) and isinstance(window.get("holdout_last_timestamp"), int): result[("HOLDOUT", symbol)] = (window["holdout_first_timestamp"], window["holdout_last_timestamp"])
    return result


def _load_context(boundaries: dict, loader: Callable[..., pd.DataFrame]) -> tuple[dict, list[dict]]:
    contexts, diagnostics = {}, []
    for (period, symbol), (start, end) in boundaries.items():
        frame = loader(symbol, "4h", end + TIMEFRAME_MS, total_limit=WINDOW_BARS + CONTEXT_BARS)
        features = _frame_features(frame)
        contexts[(period, symbol)] = features
        diagnostics.append({"period": period, "symbol": symbol, "window_start": start, "window_end": end, "requested_context_rows": WINDOW_BARS + CONTEXT_BARS, "loaded_rows": len(frame), "feature_timestamps": len(features)})
    return contexts, diagnostics


def _attach_market(records: list[dict], contexts: dict) -> tuple[list[dict], dict]:
    enriched, missing = [], 0
    by_period_timestamp: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for record in records: by_period_timestamp[(record["period"], record["ready_timestamp"])].append(record)
    for (period, timestamp), events in by_period_timestamp.items():
        btc = contexts.get((period, "BTC/USDT"), {}).get(timestamp)
        available = []
        for (context_period, _), features in contexts.items():
            if context_period != period: continue
            feature = features.get(timestamp)
            if feature: available.append(feature)
        if not btc or not available:
            missing += len(events); continue
        breadth = {"breadth_above_ema50": sum(item["close"] > item["ema50"] for item in available) / len(available), "breadth_positive_20": sum(item["return_20"] > 0 for item in available) / len(available), "median_return_20": statistics.median(item["return_20"] for item in available), "return_dispersion_20": statistics.pstdev(item["return_20"] for item in available) if len(available) > 1 else 0.0, "available_symbols": len(available)}
        for event in events:
            enriched.append({**event, **btc, **breadth, "market_regime": classify_regime(btc, breadth)})
    prior_counts = [record["prior_atr_observations"] for record in enriched]
    return enriched, {"missing_exact_timestamp_events": missing, "median_available_symbols": statistics.median([record["available_symbols"] for record in enriched]) if enriched else None, "min_prior_atr_observations": min(prior_counts) if prior_counts else None, "events_with_prior_atr_lt_200": sum(count < 200 for count in prior_counts)}


def _table(records: list[dict], keys: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, list] = defaultdict(list)
    for record in records: groups[tuple(record[key] for key in keys)].append(record)
    return [{**dict(zip(keys, key)), **_metrics(value)} for key, value in sorted(groups.items())]


def _hypothesis(rows: list[dict], regime: str, favored: str, relative: bool) -> str:
    data = {(row["period"], row["direction"]): row for row in rows if row["market_regime"] == regime}
    other = "SHORT" if favored == "LONG" else "LONG"
    values = [data.get((period, favored)) for period in PERIODS]
    if relative:
        values.extend(data.get((period, other)) for period in PERIODS)
    if any(row is None or row["n"] < MIN_SAMPLE for row in values): return "INSUFFICIENT_SAMPLE"
    favored_edges = [data[(period, favored)]["mfe_minus_mae_pct"] for period in PERIODS]
    other_edges = [data[(period, other)]["mfe_minus_mae_pct"] for period in PERIODS]
    if all(edge > 0 for edge in favored_edges) and (not relative or all(left > right for left, right in zip(favored_edges, other_edges))): return "SUPPORTED"
    if any(edge <= 0 for edge in favored_edges) or (relative and any(left <= right for left, right in zip(favored_edges, other_edges))): return "NOT_SUPPORTED"
    return "MIXED"


def _continuous(records: list[dict]) -> list[dict]:
    defs = (("btc_return_60", "return_60", [-1, 0, 1], ["<-1", "-1..0", "0..1", ">=1"]), ("ema_distance", "ema_distance", [0], ["<0", ">=0"]), ("breadth_above_ema50", "breadth_above_ema50", [.4, .6], ["<.4", ".4-.6", ">=.6"]), ("median_return_20", "median_return_20", [0], ["<0", ">=0"]), ("atr_pct", "atr_pct", [1, 2], ["<1", "1-2", ">=2"]))
    rows = []
    for name, field, edges, labels in defs:
        for label_index, label in enumerate(labels):
            def belongs(record):
                value = record[field]; lower = edges[label_index - 1] if label_index else -math.inf; upper = edges[label_index] if label_index < len(edges) else math.inf
                return lower <= value < upper
            for direction in DIRECTIONS:
                period_rows = {period: _metrics([record for record in records if record["period"] == period and record["direction"] == direction and belongs(record)]) for period in PERIODS}
                stable = all(period_rows[period]["n"] >= MIN_SAMPLE and period_rows[period]["mfe_minus_mae_pct"] != 0 for period in PERIODS) and period_rows["CURRENT"]["mfe_minus_mae_pct"] * period_rows["HOLDOUT"]["mfe_minus_mae_pct"] > 0
                rows.append({"feature": name, "bucket": label, "direction": direction, "metrics": period_rows, "stable_same_sign": stable})
    return rows


def analyze_market_regimes(current_payload: dict, holdout_payload: dict, loader: Callable[..., pd.DataFrame] = get_research_data_before) -> dict:
    current, current_skip = _records(copy.deepcopy(current_payload), "CURRENT"); holdout, holdout_skip = _records(copy.deepcopy(holdout_payload), "HOLDOUT")
    records = current + holdout
    boundaries = _boundaries(current_payload, holdout_payload, {record["symbol"] for record in records})
    contexts, context_diag = _load_context(boundaries, loader)
    enriched, coverage = _attach_market(records, contexts)
    regime_rows, volatility_rows = _table(enriched, ("period", "market_regime", "direction")), _table(enriched, ("period", "market_regime", "volatility", "direction"))
    continuous = _continuous(enriched)
    actual_rows = [item["loaded_rows"] for item in context_diag]
    return {"coverage": {"input_events": len(records), "analyzed_events": len(enriched), "skipped_malformed": {"CURRENT": current_skip, "HOLDOUT": holdout_skip}, **coverage, "requested_context_rows": WINDOW_BARS + CONTEXT_BARS, "actual_loaded_rows_distribution": {"min": min(actual_rows) if actual_rows else None, "median": statistics.median(actual_rows) if actual_rows else None, "max": max(actual_rows) if actual_rows else None}, "context_windows": context_diag}, "regime_distribution": _table(enriched, ("period", "market_regime")), "period_regime_direction": regime_rows, "hypotheses": {"H1_long_bull_positive": _hypothesis(regime_rows, "BULL", "LONG", False), "H2_short_bear_positive": _hypothesis(regime_rows, "BEAR", "SHORT", False), "H3_bull_long_over_short": _hypothesis(regime_rows, "BULL", "LONG", True), "H4_bear_short_over_long": _hypothesis(regime_rows, "BEAR", "SHORT", True)}, "stable_continuous_features": [row for row in continuous if row["stable_same_sign"]], "continuous_features": continuous, "volatility_interactions": volatility_rows}


def save_report_json(path: str | Path, report: dict) -> None:
    Path(path).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")


def save_report_csv(path: str | Path, report: dict) -> None:
    rows = [{"section": "regime", **row} for row in report["period_regime_direction"]] + [{"section": "volatility", **row} for row in report["volatility_interactions"]]
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row})); writer.writeheader(); writer.writerows(rows)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def build_console_summary(report: dict) -> str:
    coverage = report["coverage"]
    rows = coverage["actual_loaded_rows_distribution"]
    lines = ["A. Coverage / diagnostics", f"input={coverage['input_events']} analyzed={coverage['analyzed_events']} exact-missing={coverage['missing_exact_timestamp_events']} median breadth symbols={_fmt(coverage['median_available_symbols'])}", f"requested context rows={coverage['requested_context_rows']}; actual rows min/median/max={rows['min']}/{rows['median']}/{rows['max']}; min prior ATR={coverage['min_prior_atr_observations']}; events prior ATR <200={coverage['events_with_prior_atr_lt_200']}", "B. Regime distribution by period"]
    lines.extend(f"{row['period']} {row['market_regime']}: N={row['n']} edge={_fmt(row['mfe_minus_mae_pct'])}" for row in report["regime_distribution"])
    for regime, label in (("BULL", "C. BULL LONG vs SHORT"), ("BEAR", "D. BEAR LONG vs SHORT"), ("MIXED", "E. MIXED LONG vs SHORT")):
        lines.append(label)
        for row in report["period_regime_direction"]:
            if row["market_regime"] == regime:
                lines.append(f"{row['period']} {row['direction']}: N={row['n']} edge={_fmt(row['mfe_minus_mae_pct'])} close={_fmt(row['avg_close_return_pct'])}{' low-sample' if row['low_sample'] else ''}")
    lines.extend(["F. H1-H4 verdicts", *[f"{name}: {value}" for name, value in report["hypotheses"].items()], "G. Stable continuous-feature groups"])
    lines.extend(f"{row['direction']} {row['feature']} {row['bucket']}: CURRENT={_fmt(row['metrics']['CURRENT']['mfe_minus_mae_pct'])}; HOLDOUT={_fmt(row['metrics']['HOLDOUT']['mfe_minus_mae_pct'])}" for row in report["stable_continuous_features"])
    lines.append("H. Volatility interactions")
    lines.extend(f"{row['period']} {row['market_regime']} {row['volatility']} {row['direction']}: N={row['n']} edge={_fmt(row['mfe_minus_mae_pct'])}" for row in report["volatility_interactions"] if not row["low_sample"])
    return "\n".join(lines)


def main() -> None:
    report = analyze_market_regimes(json.loads(Path(CURRENT_PATH).read_text()), json.loads(Path(HOLDOUT_PATH).read_text()))
    save_report_json(OUTPUT_JSON, report); save_report_csv(OUTPUT_CSV, report)
    print(build_console_summary(report))


if __name__ == "__main__": main()