"""Pre-registered HOLDOUT2 regime validation using shared market definitions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pandas as pd

from ready_market_regime_analysis import CONTEXT_BARS, WINDOW_BARS, _attach_market, _frame_features, _metrics, _records, _table
from research_data import get_research_data_before

HOLDOUT2_PATH = "artifacts/trendscanner/ready_outcome_holdout2_results.json"
OUTPUT_JSON = "artifacts/trendscanner/ready_regime_validation_holdout2.json"
OUTPUT_CSV = "artifacts/trendscanner/ready_regime_validation_holdout2.csv"
MIN_SAMPLE = 15
PREDICTIONS = {"P1_bear_high_short": ("BEAR", "HIGH", "SHORT", 1), "P2_bull_normal_long": ("BULL", "NORMAL", "LONG", 1), "P3_mixed_normal_long": ("MIXED", "NORMAL", "LONG", 1), "P4_mixed_normal_short": ("MIXED", "NORMAL", "SHORT", -1)}


def prediction_verdict(row: dict, expected_sign: int) -> str:
    edge = row["mfe_minus_mae_pct"]
    if row["n"] < MIN_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    return "SUPPORTED" if edge * expected_sign > 0 else "NOT_SUPPORTED"


def _boundaries(payload: dict) -> dict:
    output = {}
    for run in payload.get("runs", []):
        window = run.get("holdout2_window", {}) if isinstance(run, dict) else {}
        if run.get("status") == "OK" and isinstance(window, dict) and isinstance(window.get("holdout2_first_timestamp"), int) and isinstance(window.get("holdout2_last_timestamp"), int): output[run["symbol"]] = (window["holdout2_first_timestamp"], window["holdout2_last_timestamp"])
    return output


def validate_holdout2(payload: dict, loader: Callable[..., pd.DataFrame] = get_research_data_before) -> dict:
    records, skipped = _records(payload, "HOLDOUT2"); boundaries = _boundaries(payload); contexts = {}
    for symbol, (_, end) in boundaries.items(): contexts[("HOLDOUT2", symbol)] = _frame_features(loader(symbol, "4h", end + 14_400_000, total_limit=WINDOW_BARS + CONTEXT_BARS))
    enriched, diagnostics = _attach_market(records, contexts)
    rows = _table(enriched, ("market_regime", "volatility", "direction")); lookup = {(row["market_regime"], row["volatility"], row["direction"]): row for row in rows}
    verdicts = {}
    for name, (regime, volatility, direction, expected_sign) in PREDICTIONS.items():
        row = lookup.get((regime, volatility, direction), _metrics([]))
        verdicts[name] = {**row, "verdict": prediction_verdict(row, expected_sign)}
    return {"coverage": {"input_events": len(records), "analyzed_events": len(enriched), "skipped_malformed": skipped, **diagnostics}, "date_ranges": {symbol: {"start": bounds[0], "end": bounds[1]} for symbol, bounds in boundaries.items()}, "overall": _table(enriched, ("direction",)), "regime_volatility_direction": rows, "predictions": verdicts}


def main() -> None:
    report = validate_holdout2(json.loads(Path(HOLDOUT2_PATH).read_text(encoding="utf-8")))
    Path(OUTPUT_JSON).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    with Path(OUTPUT_CSV).open("w", encoding="utf-8") as handle: handle.write("market_regime,volatility,direction,n,mfe_minus_mae_pct\n" + "\n".join(f"{row['market_regime']},{row['volatility']},{row['direction']},{row['n']},{row['mfe_minus_mae_pct']}" for row in report["regime_volatility_direction"]))
    print(json.dumps({"coverage": report["coverage"], "predictions": report["predictions"]}, indent=2))


if __name__ == "__main__": main()