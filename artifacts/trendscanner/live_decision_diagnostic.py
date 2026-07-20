"""
live_decision_diagnostic.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Диагностика Trade Decision Engine на реальных данных.

Символы: BTC/USDT 1h, BTC/USDT 4h, XRP/USDT 4h

Запуск:
    python live_decision_diagnostic.py

Не является unit-тестом.
"""

from __future__ import annotations
import sys, requests, pandas as pd

sys.path.insert(0, ".")
from quality_pipeline import analyze_both_directions
from decision_engine import evaluate_both_directions

BASE = "https://api-futures.kucoin.com"


def fetch_klines(symbol: str, gran_min: int, limit: int = 200) -> pd.DataFrame:
    url = (
        f"{BASE}/api/v1/kline/query"
        f"?symbol={symbol}&granularity={gran_min}&dataLen={limit}"
    )
    r = requests.get(url, timeout=15)
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError(f"No data for {symbol}")
    df = pd.DataFrame(
        data,
        columns=["ts", "open", "close", "high", "low", "volume", "turnover"],
    )
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c])
    df = df.sort_values("ts").reset_index(drop=True)
    return df


CASES = [
    ("BTC/USDT", "XBTUSDTM", 60,  "1h"),
    ("BTC/USDT", "XBTUSDTM", 240, "4h"),
    ("XRP/USDT", "XRPUSDTM", 240, "4h"),
]


def _fmt_decision(d: dict) -> str:
    dec   = d.get("decision", "?")
    score = d.get("decision_score", "?")
    conf  = d.get("confidence", "?")
    conf_s = f"{conf:.1f}" if isinstance(conf, float) else str(conf)
    reason = d.get("reason", "")[:80]
    blk = [b["code"] for b in d.get("blockers", [])]
    blk_s = f" BLOCKERS={blk}" if blk else ""
    return f"{dec} | score={score} | conf={conf_s}{blk_s} | {reason}"


def run():
    print("\n" + "═" * 72)
    print("  LIVE DECISION DIAGNOSTIC (v0.5)")
    print("═" * 72)

    for display_sym, api_sym, gran_min, tf_label in CASES:
        print(f"\n  ── {display_sym} {tf_label} ──────────────────────────────────────")
        try:
            df = fetch_klines(api_sym, gran_min)
            qa = analyze_both_directions(df)
            de = evaluate_both_directions(qa)

            # Pipeline summary
            pf = qa.get("FINAL", {})
            p_sig  = pf.get("signal", "?")
            p_conf = pf.get("confidence", "?")
            p_lab  = pf.get("label", "?")
            print(f"  Pipeline FINAL: signal={p_sig} | conf={p_conf} | {p_lab}")

            # Decision Engine
            print(f"  LONG  → {_fmt_decision(de['LONG'])}")
            print(f"  SHORT → {_fmt_decision(de['SHORT'])}")

            fin = de["FINAL"]
            print(
                f"  FINAL → {fin.get('decision','?')} | "
                f"direction={fin.get('direction','?')} | "
                f"score={fin.get('decision_score','?')} | "
                f"conf={fin.get('confidence','?')} | "
                f"{fin.get('reason','')[:70]}"
            )

            # Warn / positive для LONG
            long_pos  = de["LONG"].get("positive_factors", [])
            long_warn = de["LONG"].get("warning_factors", [])
            if long_pos:
                print(f"  LONG  (+) {long_pos}")
            if long_warn:
                print(f"  LONG  (!) {long_warn}")

        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()

    print("\n" + "═" * 72)


if __name__ == "__main__":
    run()
