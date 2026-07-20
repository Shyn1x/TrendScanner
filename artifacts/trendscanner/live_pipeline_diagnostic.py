"""
live_pipeline_diagnostic.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Диагностика полного пайплайна на реальных данных KuCoin.

Использует get_data() для получения реальных OHLCV-данных,
затем запускает analyze_timeframe и analyze_both_directions.

Запуск:
    python live_pipeline_diagnostic.py

Не является частью unit-тестов (делает реальные запросы к бирже).
"""

import sys
import os
import json

# Убедимся, что импорты берутся из текущей директории
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner          import get_data
from analysis         import analyze_timeframe
from quality_pipeline import analyze_both_directions, PIPELINE_VERSION


CASES = [
    ("BTC/USDT", "1h"),
    ("BTC/USDT", "4h"),
    ("XRP/USDT", "4h"),
]


def _safe_get(d, *keys, default=None):
    """Безопасный многоуровневый get."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def run_diagnostic(symbol: str, timeframe: str) -> dict:
    """
    Получает реальные данные и запускает полный пайплайн.

    Возвращает компактный отчёт без всего DataFrame.
    """
    df = get_data(symbol, timeframe)
    both   = analyze_both_directions(df)
    result = analyze_timeframe(df)

    long_r  = both.get("LONG",  {})
    short_r = both.get("SHORT", {})

    def extract_dir(d: dict) -> dict:
        conf = d.get("confidence", {})
        conf_val = conf.get("confidence") if isinstance(conf, dict) else None

        bq   = d.get("breakout_quality", {})
        tq   = d.get("trend_quality",    {})
        vq   = d.get("volume_quality",   {})

        bq_score = bq.get("breakout_score") if isinstance(bq, dict) else None
        bq_cross = _safe_get(bq, "components", "cross") if isinstance(bq, dict) else None
        tq_score = tq.get("trend_quality_score") if isinstance(tq, dict) else None
        vq_score = vq.get("volume_score") if isinstance(vq, dict) else None

        return {
            "signal":         d.get("signal"),
            "confirmed":      bool(d.get("confirmed", False)),
            "confidence":     conf_val,
            "reason":         d.get("reason"),
            "trend_score":    tq_score,
            "volume_score":   vq_score,
            "breakout_score": bq_score,
            "breakout_cross": bq_cross,
            "line_present":   d.get("line") is not None,
        }

    last_ts = "N/A"
    if "time" in df.columns and len(df) > 0:
        last_ts = str(df["time"].iloc[-1])

    pipeline_final = both.get("FINAL", {})

    return {
        "symbol":             symbol,
        "timeframe":          timeframe,
        "pipeline_version":   PIPELINE_VERSION,
        "rows":               len(df),
        "last_timestamp":     last_ts,
        "analysis_signal":    result.get("signal"),
        "analysis_confidence": result.get("confidence"),
        "analysis_label":     result.get("confidence_label"),
        "analysis_reason":    result.get("reason"),
        "pipeline_final":     pipeline_final,
        "long":               extract_dir(long_r),
        "short":              extract_dir(short_r),
    }


def _check_invariant(report: dict) -> list[str]:
    """Проверяет инварианты на результате диагностики. Возвращает список нарушений."""
    violations = []
    sig  = report.get("analysis_signal")
    conf = report.get("analysis_confidence")

    if sig in ("LONG", "SHORT"):
        if conf is None or conf < 50:
            violations.append(
                f"INVARIANT VIOLATION: signal={sig} but confidence={conf} < 50"
            )
        direction_data = report.get(sig.lower(), {})
        if not direction_data.get("confirmed"):
            violations.append(
                f"INVARIANT VIOLATION: signal={sig} but {sig}.confirmed=False"
            )
        pf_signal = _safe_get(report, "pipeline_final", "signal")
        if pf_signal != sig:
            violations.append(
                f"INVARIANT VIOLATION: analysis.signal={sig} != pipeline_final.signal={pf_signal}"
            )

    return violations


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print(f"  LIVE PIPELINE DIAGNOSTIC  (version: {PIPELINE_VERSION})")
    print(f"{'='*60}\n")

    all_ok = True
    for symbol, timeframe in CASES:
        print(f"\n{'─'*60}")
        print(f"  {symbol}  {timeframe}")
        print(f"{'─'*60}")
        try:
            report = run_diagnostic(symbol, timeframe)
            # Компактный JSON (без pipeline_final — он большой)
            display = {k: v for k, v in report.items() if k != "pipeline_final"}
            display["pipeline_final_signal"]     = _safe_get(report, "pipeline_final", "signal")
            display["pipeline_final_confidence"] = _safe_get(report, "pipeline_final", "confidence")
            print(json.dumps(display, indent=2, default=str))

            violations = _check_invariant(report)
            if violations:
                all_ok = False
                for v in violations:
                    print(f"\n  ❌ {v}")
            else:
                print(f"\n  ✅ Invariant OK")

        except Exception as e:
            all_ok = False
            print(f"\n  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    status = "✅ ALL CASES PASSED" if all_ok else "❌ VIOLATIONS FOUND"
    print(f"  {status}")
    print(f"{'='*60}\n")
