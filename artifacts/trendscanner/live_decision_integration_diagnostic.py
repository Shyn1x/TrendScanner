"""
live_decision_integration_diagnostic.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Диагностика интеграции Decision Engine в analysis.py и multi_tf.py.

Символы: BTC/USDT 1h, BTC/USDT 4h, XRP/USDT 4h (одиночные TF)
MTF:     BTC/USDT (multi_analysis)

Запуск:
    python live_decision_integration_diagnostic.py
"""

from __future__ import annotations
import sys, requests, pandas as pd

sys.path.insert(0, ".")
from analysis  import analyze_timeframe
from multi_tf  import multi_analysis

BASE = "https://api-futures.kucoin.com"
GRAN = {"1h": 60, "4h": 240}


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
    return df.sort_values("ts").reset_index(drop=True)


SINGLE_TF_CASES = [
    ("BTC/USDT", "XBTUSDTM", "1h"),
    ("BTC/USDT", "XBTUSDTM", "4h"),
    ("XRP/USDT", "XRPUSDTM", "4h"),
]


def _bar(label: str, value: str) -> str:
    return f"  {label:<24} {value}"


def run_single_tf():
    print("\n" + "═"*72)
    print("  SINGLE-TF ANALYSIS+DECISION DIAGNOSTIC (v0.5)")
    print("═"*72)

    for display_sym, api_sym, tf in SINGLE_TF_CASES:
        print(f"\n  ── {display_sym} {tf} ──────────────────────────────────────────")
        try:
            df = fetch_klines(api_sym, GRAN[tf])
            r  = analyze_timeframe(df)

            print(_bar("signal / confidence:",
                       f"{r['signal']} / {r['confidence']:.1f} ({r['confidence_label']})"))
            print(_bar("decision / direction:",
                       f"{r['decision']} / {r['decision_direction']}"))
            print(_bar("decision_score:",
                       f"{r['decision_score']:.2f}"))
            print(_bar("decision_reason:",
                       (r['decision_reason'] or '')[:72]))

            # Компоненты решения
            det = r.get("decision_details", {})
            for side in ("LONG", "SHORT"):
                sd = det.get(side, {})
                if sd:
                    blk = [b["code"] for b in sd.get("blockers", [])]
                    blk_s = f"  BLOCKERS={blk}" if blk else ""
                    print(f"  {side:<8} dec={sd.get('decision','?')} "
                          f"score={sd.get('decision_score','?')} "
                          f"conf={sd.get('confidence','?')}{blk_s}")

        except Exception as e:
            import traceback
            print(f"  ERROR: {e}")
            traceback.print_exc()


def run_mtf():
    print("\n" + "═"*72)
    print("  MTF DECISION DIAGNOSTIC — BTC/USDT (v0.5)")
    print("═"*72)

    try:
        r   = multi_analysis("BTC/USDT")
        fin = r["FINAL"]

        print("\n  ── Per-TF Decision ───────────────────────────────────────────")
        dtc = fin.get("decision_timeframe_components", {})
        for tf in ["1M", "1w", "1d", "4h", "1h"]:
            c = dtc.get(tf, {})
            avail = c.get("available", False)
            if not avail:
                print(f"  {tf:<4}  UNAVAILABLE")
                continue
            print(f"  {tf:<4}  dec={c.get('decision','?'):<5} "
                  f"dir={c.get('direction','?'):<5} "
                  f"d_score={c.get('decision_score') or 0:.1f} "
                  f"contrib={c.get('contribution') or 0:.4f} "
                  f"eff_w={c.get('effective_weight') or 0:.4f}")

        print("\n  ── FINAL ─────────────────────────────────────────────────────")
        print(_bar("Pipeline signal:",
                   f"{fin['signal']} / conf={fin['confidence']:.1f} ({fin['confidence_label']})"))
        print(_bar("Pipeline d_score:", f"{fin['directional_score']:.4f}"))
        print(_bar("Decision:",
                   f"{fin['decision']} / {fin['decision_direction']}"))
        print(_bar("Decision score:", f"{fin['decision_score']:.2f}"))
        print(_bar("Decision dir_score:", f"{fin['decision_directional_score']:.4f}"))
        print(_bar("Decision reason:", (fin['decision_reason'] or '')[:68]))

        cnt = fin.get("decision_counts", {})
        print(_bar("Decision counts:",
                   f"TL={cnt.get('take_long',0)} TS={cnt.get('take_short',0)} "
                   f"WL={cnt.get('watch_long',0)} WS={cnt.get('watch_short',0)} "
                   f"SK={cnt.get('skip',0)}"))

    except Exception as e:
        import traceback
        print(f"  ERROR: {e}")
        traceback.print_exc()

    print("\n" + "═"*72)


if __name__ == "__main__":
    run_single_tf()
    run_mtf()
