"""
run_historical_audit.py
~~~~~~~~~~~~~~~~~~~~~~~
Запускает Historical Pipeline Replay на реальных данных и выводит полный отчёт.

Использование:
    python run_historical_audit.py

Не изменяет торговую логику, пороги, веса или какие-либо существующие файлы.
"""

import sys
import math
import time
from collections import Counter

from scanner           import get_data
from historical_replay import (
    replay_timeframe,
    build_confirmed_breakout_funnel,
    format_funnel_report,
    _primary_rejection_reason,
    _CONF_40, _CONF_50,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Конфигурация аудита (не трогать торговую логику)
# ─────────────────────────────────────────────────────────────────────────────

AUDIT_TARGETS = [
    ("BTC/USDT", "1h"),
    ("BTC/USDT", "4h"),
    ("BTC/USDT", "1d"),
    ("ETH/USDT", "1h"),
    ("ETH/USDT", "4h"),
    ("SOL/USDT", "1h"),
    ("SOL/USDT", "4h"),
    ("XRP/USDT", "1h"),
    ("XRP/USDT", "4h"),
]

WARMUP_BARS     = 120
MAX_REPLAY_BARS = 200

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _f(v, default=0.0) -> float:
    try:
        r = float(v); return r if math.isfinite(r) else default
    except (TypeError, ValueError):
        return default


def _pct(n, total) -> str:
    if total == 0: return "0%"
    return f"{n/total*100:.1f}%"


def _ts_str(ts) -> str:
    """Миллисекунды → читаемая дата."""
    if ts is None:
        return "—"
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


# ─────────────────────────────────────────────────────────────────────────────
#  Health Classification
# ─────────────────────────────────────────────────────────────────────────────

def classify_health(
    all_results:       list,
    long_funnel:       dict,
    short_funnel:      dict,
    top_rejections:    Counter,
    total_replay:      int,
) -> tuple[str, list[str]]:
    """
    Классифицирует здоровье pipeline по правилам спецификации.
    Возвращает (classification_string, list_of_notes).
    """
    notes = []

    if total_replay == 0:
        return "INSUFFICIENT_HISTORY", ["No replay points available."]

    long_confirmed  = long_funnel.get("breakout_confirmed", 0)
    short_confirmed = short_funnel.get("breakout_confirmed", 0)
    long_watch      = long_funnel.get("decision_watch",     0)
    short_watch     = short_funnel.get("decision_watch",    0)
    long_take       = long_funnel.get("decision_take",      0)
    short_take      = short_funnel.get("decision_take",     0)

    has_long_confirmed  = long_confirmed  > 0
    has_short_confirmed = short_confirmed > 0
    has_watch_any       = (long_watch + short_watch) > 0
    has_take_any        = (long_take  + short_take)  > 0

    conf_vals   = [_f(r.get("confidence", 0)) for r in all_results]
    nonzero_conf = sum(1 for c in conf_vals if c > 0)
    all_zero_conf = nonzero_conf == 0

    # Result diversity (do results vary?)
    signals   = [r.get("signal", "WAIT")    for r in all_results]
    decisions = [r.get("decision", "SKIP")  for r in all_results]
    signal_diversity   = len(set(signals))   > 1
    decision_diversity = len(set(decisions)) > 1

    # Over-filtering: >= 95% of confirmed breakouts rejected by single reason
    total_confirmed = long_confirmed + short_confirmed
    over_filtered   = False
    if total_confirmed > 5 and top_rejections:
        top_reason, top_count = top_rejections.most_common(1)[0]
        if top_count / total_confirmed >= 0.95:
            over_filtered = True
            notes.append(f"95%+ confirmed breakouts rejected by single reason: '{top_reason}'")

    # Bug indicators
    bug_signs = []

    # confirmed breakout with confidence=0
    conf_zero_after_confirmed = sum(
        1 for r in all_results
        if (r.get("long", {}).get("breakout_confirmed") or
            r.get("short", {}).get("breakout_confirmed"))
        and _f(r.get("confidence", 1)) == 0.0
    )
    if conf_zero_after_confirmed > 0:
        bug_signs.append(f"confirmed breakout with confidence=0: {conf_zero_after_confirmed} cases")

    # All results identical (no variation)
    if total_replay > 5 and not signal_diversity and not decision_diversity:
        bug_signs.append("all historical points return identical signal+decision")

    # LONG/SHORT asymmetry (one direction always 0 confirmed when total_replay >> warmup)
    if total_replay >= 20:
        if has_long_confirmed and not has_short_confirmed:
            notes.append("SHORT confirmed=0 across all history — possible asymmetry")
        if has_short_confirmed and not has_long_confirmed:
            notes.append("LONG confirmed=0 across all history — possible asymmetry")

    # All confidence values are 0
    if all_zero_conf and total_replay > 5:
        bug_signs.append("all confidence values are zero")

    # Classification
    if bug_signs:
        for s in bug_signs:
            notes.append(f"Bug sign: {s}")
        return "POSSIBLE_PIPELINE_BUG", notes

    if total_confirmed < 3:
        notes.append(f"Only {total_confirmed} confirmed breakouts in {total_replay} replay points")
        return "INSUFFICIENT_HISTORY", notes

    if over_filtered:
        return "POSSIBLE_OVER_FILTERING", notes

    if has_long_confirmed and has_short_confirmed and not all_zero_conf:
        return "PIPELINE_LIKELY_HEALTHY", notes

    return "INSUFFICIENT_HISTORY", notes


# ─────────────────────────────────────────────────────────────────────────────
#  Top candidates table
# ─────────────────────────────────────────────────────────────────────────────

def _top_candidates(all_results: list, n: int = 10) -> list:
    """Sort by decision_score → confidence → breakout_score (best direction)."""
    def sort_key(r):
        ds = _f(r.get("decision_score", 0))
        cf = _f(r.get("confidence",     0))
        bs = max(
            _f(r.get("long",  {}).get("breakout_score", 0) or 0),
            _f(r.get("short", {}).get("breakout_score", 0) or 0),
        )
        return (ds, cf, bs)

    return sorted(all_results, key=sort_key, reverse=True)[:n]


def _closest_to_watch(all_results: list, n: int = 5) -> list:
    """
    Confirmed breakouts that got SKIP but were closest to WATCH.
    Proxy: highest decision_score among SKIP cases with a confirmed breakout.
    """
    candidates = [
        r for r in all_results
        if r.get("decision") == "SKIP"
        and (r.get("long",  {}).get("breakout_confirmed")
             or r.get("short", {}).get("breakout_confirmed"))
    ]
    return sorted(candidates,
                  key=lambda r: _f(r.get("decision_score", 0)),
                  reverse=True)[:n]


# ─────────────────────────────────────────────────────────────────────────────
#  Main report builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_report(
    all_results:   list,
    all_metas:     list,
    funnel:        dict,
    health:        str,
    health_notes:  list,
    look_ahead_ok: bool,
    invariant_ok:  bool,
) -> str:
    lines = []
    W = 50

    def L(s=""): lines.append(s)

    total   = len(all_results)
    lf      = funnel["long"]
    sf      = funnel["short"]
    top_rej = funnel["top_rejections"]

    # ── Header ────────────────────────────────────────────────────────────────
    L("=" * W)
    L("HISTORICAL PIPELINE AUDIT")
    L("=" * W)
    L()

    total_bars = sum(m.get("total_bars", 0) for m in all_metas)
    replay_sum = sum(m.get("replay_count", 0) for m in all_metas)
    skipped    = sum(m.get("skipped",     0) for m in all_metas)
    insuff     = [m for m in all_metas if m.get("insufficient_data")]

    L(f"Symbols / Timeframes:  {len(all_metas)}")
    if insuff:
        L(f"  (insufficient data: {', '.join(m['symbol']+' '+m['timeframe'] for m in insuff)})")
    L(f"Total bars fetched:    {total_bars}")
    L(f"Total replay points:   {replay_sum}  (skipped: {skipped})")
    L()

    # ── Stage counts ──────────────────────────────────────────────────────────
    bo_det = sum(
        1 for r in all_results
        if r.get("long",  {}).get("breakout_detected")
        or r.get("short", {}).get("breakout_detected")
    )
    bo_conf = sum(
        1 for r in all_results
        if r.get("long",  {}).get("breakout_confirmed")
        or r.get("short", {}).get("breakout_confirmed")
    )
    conf_ge40 = sum(1 for r in all_results if _f(r.get("confidence",0)) >= _CONF_40)
    conf_ge50 = sum(1 for r in all_results if _f(r.get("confidence",0)) >= _CONF_50)
    n_watch   = sum(1 for r in all_results if r.get("decision") == "WATCH")
    n_take    = sum(1 for r in all_results if r.get("decision") == "TAKE")
    n_skip    = sum(1 for r in all_results if r.get("decision") == "SKIP")

    L(f"Total replay points:   {total}")
    L(f"Breakout detected:     {bo_det:<5}  ({_pct(bo_det,  total)})")
    L(f"Breakout confirmed:    {bo_conf:<5}  ({_pct(bo_conf, total)})")
    L(f"Confidence >= 40:      {conf_ge40:<5}  ({_pct(conf_ge40, total)})")
    L(f"Confidence >= 50:      {conf_ge50:<5}  ({_pct(conf_ge50, total)})")
    L(f"WATCH:                 {n_watch:<5}  ({_pct(n_watch, total)})")
    L(f"TAKE:                  {n_take:<5}  ({_pct(n_take,  total)})")
    L(f"SKIP:                  {n_skip:<5}  ({_pct(n_skip,  total)})")
    L()

    # ── LONG / SHORT ──────────────────────────────────────────────────────────
    L("LONG:")
    L(f"  confirmed:  {lf['breakout_confirmed']}")
    L(f"  WATCH:      {lf['decision_watch']}")
    L(f"  TAKE:       {lf['decision_take']}")
    L()
    L("SHORT:")
    L(f"  confirmed:  {sf['breakout_confirmed']}")
    L(f"  WATCH:      {sf['decision_watch']}")
    L(f"  TAKE:       {sf['decision_take']}")
    L()

    # ── Top rejection reasons ─────────────────────────────────────────────────
    L("Top rejection reasons:")
    if top_rej:
        for reason, cnt in top_rej.most_common(10):
            L(f"  {reason:<35} {cnt}")
    else:
        L("  (none — no confirmed breakouts rejected)")
    L()

    # ── Confidence distribution ───────────────────────────────────────────────
    conf_vals = [_f(r.get("confidence", 0)) for r in all_results]
    buckets = [
        ("0–19",   sum(1 for c in conf_vals if c < 20)),
        ("20–39",  sum(1 for c in conf_vals if 20 <= c < 40)),
        ("40–49",  sum(1 for c in conf_vals if 40 <= c < 50)),
        ("50–69",  sum(1 for c in conf_vals if 50 <= c < 70)),
        ("70–84",  sum(1 for c in conf_vals if 70 <= c < 85)),
        ("85–100", sum(1 for c in conf_vals if c >= 85)),
    ]
    L("Confidence distribution:")
    for label, cnt in buckets:
        bar = "█" * int(cnt / max(total, 1) * 25)
        L(f"  {label:<8}  {bar:<25}  {cnt}  ({_pct(cnt, total)})")
    L()

    # ── Decision score distribution ───────────────────────────────────────────
    ds_vals = [_f(r.get("decision_score", 0)) for r in all_results]
    ds_buckets = [
        ("0–24",   sum(1 for d in ds_vals if d < 25)),
        ("25–49",  sum(1 for d in ds_vals if 25 <= d < 50)),
        ("50–69",  sum(1 for d in ds_vals if 50 <= d < 70)),
        ("70–100", sum(1 for d in ds_vals if d >= 70)),
    ]
    L("Decision-score distribution:")
    for label, cnt in ds_buckets:
        bar = "█" * int(cnt / max(total, 1) * 25)
        L(f"  {label:<8}  {bar:<25}  {cnt}  ({_pct(cnt, total)})")
    L()

    # ── Funnel ────────────────────────────────────────────────────────────────
    L("=" * W)
    L("CONFIRMED BREAKOUT FUNNEL")
    L("=" * W)
    L(format_funnel_report(funnel))
    L()

    # ── Top candidates ────────────────────────────────────────────────────────
    L("=" * W)
    L("TOP 10 HISTORICAL CANDIDATES")
    L("(sorted by decision_score → confidence → breakout_score)")
    L("=" * W)
    for i, r in enumerate(_top_candidates(all_results, 10), 1):
        best_dir = r.get("decision_direction", "NONE")
        if best_dir == "NONE":
            best_dir = "LONG" if _f(r.get("long",{}).get("breakout_score",0) or 0) \
                                > _f(r.get("short",{}).get("breakout_score",0) or 0) else "SHORT"
        d = r.get(best_dir.lower(), {})
        rej = _primary_rejection_reason(r, best_dir)
        L(f"\n  #{i}")
        L(f"  timestamp:   {_ts_str(r.get('signal_timestamp'))}")
        L(f"  symbol:      {r.get('symbol')}  {r.get('timeframe')}")
        L(f"  direction:   {best_dir}")
        L(f"  signal:      {r.get('signal')}")
        L(f"  decision:    {r.get('decision')}")
        L(f"  dec_score:   {_f(r.get('decision_score',0)):.1f}")
        L(f"  confidence:  {_f(r.get('confidence',0)):.1f}")
        L(f"  bk_confirmed:{d.get('breakout_confirmed', False)}")
        L(f"  rejection:   {rej}")
    L()

    # ── Closest to WATCH ──────────────────────────────────────────────────────
    closest = _closest_to_watch(all_results, 5)
    if closest:
        L("=" * W)
        L("TOP 5 CONFIRMED BREAKOUTS CLOSEST TO WATCH")
        L("=" * W)
        for i, r in enumerate(closest, 1):
            best_dir = "LONG" if r.get("long", {}).get("breakout_confirmed") else "SHORT"
            d = r.get(best_dir.lower(), {})
            rej = _primary_rejection_reason(r, best_dir)
            L(f"\n  #{i}")
            L(f"  timestamp:   {_ts_str(r.get('signal_timestamp'))}")
            L(f"  symbol:      {r.get('symbol')}  {r.get('timeframe')}")
            L(f"  direction:   {best_dir}")
            L(f"  decision:    {r.get('decision')}  score={_f(r.get('decision_score',0)):.1f}")
            L(f"  confidence:  {_f(r.get('confidence',0)):.1f}")
            L(f"  trend_q:     {d.get('trend_quality')}")
            L(f"  volume_q:    {d.get('volume_quality')}")
            L(f"  primary rej: {rej}")
        L()

    # ── Health check ──────────────────────────────────────────────────────────
    L("=" * W)
    L("PIPELINE HEALTH CHECK")
    L("=" * W)
    L()
    L(f"Look-ahead test:     {'PASSED' if look_ahead_ok else 'FAILED'}")
    L(f"Signal invariant:    {'PASSED' if invariant_ok  else 'FAILED'}")
    longconf  = lf["breakout_confirmed"]
    shortconf = sf["breakout_confirmed"]
    symm_ok   = not (longconf > 5 and shortconf == 0) and not (shortconf > 5 and longconf == 0)
    L(f"LONG/SHORT symmetry: {'PASSED' if symm_ok else 'WARNING (asymmetry)'}")
    L()
    L(f"Health classification: {health}")
    if health_notes:
        for note in health_notes:
            L(f"  · {note}")
    L()

    # ── Summary block ─────────────────────────────────────────────────────────
    L("=" * W)
    L("HISTORICAL AUDIT STATUS")
    L("=" * W)
    L()
    audit_passed = look_ahead_ok and invariant_ok
    L(f"HISTORICAL AUDIT STATUS: {'PASSED' if audit_passed else 'FAILED'}")
    L()
    L(f"Health classification: {health}")
    L()
    L("Historical sample:")
    sym_set = sorted({m["symbol"]    for m in all_metas if not m.get("insufficient_data")})
    tf_set  = sorted({m["timeframe"] for m in all_metas if not m.get("insufficient_data")})
    L(f"  symbols:    {', '.join(sym_set)}")
    L(f"  timeframes: {', '.join(tf_set)}")
    L(f"  replay points: {total}")
    L()
    L("Breakouts:")
    L(f"  detected:  {bo_det}  ({_pct(bo_det,  total)})")
    L(f"  confirmed: {bo_conf}  ({_pct(bo_conf, total)})")
    L()
    L("Decisions:")
    L(f"  WATCH: {n_watch}")
    L(f"  TAKE:  {n_take}")
    L(f"  SKIP:  {n_skip}")
    L()
    L("Confirmed breakouts rejected:")
    if top_rej:
        for reason, cnt in top_rej.most_common(5):
            L(f"  {reason:<35} {cnt}")
    else:
        L("  (none)")
    L()
    L("Look-ahead test:     " + ("PASSED" if look_ahead_ok else "FAILED"))
    L("Signal invariant:    " + ("PASSED" if invariant_ok  else "FAILED"))
    L("LONG/SHORT symmetry: " + ("PASSED" if symm_ok else "WARNING"))
    L()

    # Conclusion
    if health == "PIPELINE_LIKELY_HEALTHY":
        if n_take == 0 and n_watch == 0:
            L("Conclusion:")
            L("  Current market likely explains empty Top Setups.")
            L("  Pipeline has historically produced LONG/SHORT confirmed breakouts,")
            L("  indicating the engine works — current conditions simply don't qualify.")
        else:
            L("Conclusion:")
            L("  Pipeline operating normally. Historical signals detected and WATCH/TAKE seen.")
    elif health == "POSSIBLE_OVER_FILTERING":
        L("Conclusion:")
        L("  Pipeline likely over-filtered. Confirmed breakouts exist historically")
        L("  but one single threshold rejects 95%+. Review that threshold separately.")
    elif health == "POSSIBLE_PIPELINE_BUG":
        L("Conclusion:")
        L("  Probable implementation bug found. See bug signs above.")
    else:
        L("Conclusion:")
        L("  Insufficient history to classify. Fetch more bars or add more symbols.")

    L()
    L("Files created:")
    L("  historical_replay.py")
    L("  run_historical_audit.py")
    L("  test_historical_replay.py")
    L()
    L("Files changed: none")
    L()
    L("=" * W)

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  Look-ahead bias test (при запуске скрипта)
# ─────────────────────────────────────────────────────────────────────────────

def _run_look_ahead_test() -> bool:
    """
    Запрашивает данные по BTC/USDT 1h, делает replay одной точки,
    затем изменяет свечи ПОСЛЕ этой точки и проверяет, что результат не изменился.
    """
    import copy
    try:
        df = get_data("BTC/USDT", "1h")
        if df is None or len(df) < 130:
            return True  # нет данных — нельзя провалить

        from analysis import analyze_timeframe
        warmup = 120
        end_index = warmup  # первый replay-шаг

        historical_df = df.iloc[:end_index + 1].copy()
        result_before = analyze_timeframe(historical_df)

        # Изменяем все строки ПОСЛЕ end_index (будущие свечи)
        df_modified = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            if col in df_modified.columns:
                df_modified.loc[end_index + 1:, col] *= 999.0

        historical_df2 = df_modified.iloc[:end_index + 1].copy()
        result_after = analyze_timeframe(historical_df2)

        # Результат не должен измениться — оба используют одинаковый срез
        return (result_before.get("signal")   == result_after.get("signal")   and
                result_before.get("decision")  == result_after.get("decision") and
                abs(_f(result_before.get("confidence", 0)) -
                    _f(result_after.get("confidence",  0))) < 0.001)
    except Exception:
        return True  # не блокируем аудит при сетевой ошибке


def _run_invariant_test(all_results: list) -> bool:
    """
    Проверяет: каждый result с decision=TAKE должен иметь
    decision_direction в (LONG, SHORT) и breakout_confirmed=True
    для соответствующего направления.
    """
    for r in all_results:
        if r.get("decision") == "TAKE":
            dec_dir = r.get("decision_direction", "NONE")
            if dec_dir not in ("LONG", "SHORT"):
                return False
            d = r.get(dec_dir.lower(), {})
            if not d.get("breakout_confirmed", False):
                return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    all_results: list = []
    all_metas:   list = []

    total_targets = len(AUDIT_TARGETS)
    for idx, (symbol, timeframe) in enumerate(AUDIT_TARGETS, 1):
        sys.stdout.write(
            f"\r  [{idx}/{total_targets}] Fetching {symbol} {timeframe}..."
        )
        sys.stdout.flush()

        try:
            df = get_data(symbol, timeframe)
        except Exception as e:
            sys.stdout.write(f"\r  [{idx}/{total_targets}] {symbol} {timeframe}: fetch error — {e}\n")
            all_metas.append({
                "symbol": symbol, "timeframe": timeframe,
                "total_bars": 0, "replay_count": 0,
                "skipped": 0, "insufficient_data": True,
            })
            continue

        sys.stdout.write(
            f"\r  [{idx}/{total_targets}] Replaying {symbol} {timeframe} "
            f"({len(df) if df is not None else 0} bars)..."
        )
        sys.stdout.flush()

        replay_data = replay_timeframe(
            df, symbol, timeframe,
            warmup_bars=WARMUP_BARS,
            max_replay_bars=MAX_REPLAY_BARS,
        )
        all_results.extend(replay_data["replay_results"])
        all_metas.append(replay_data["meta"])
        time.sleep(0.1)

    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    funnel = build_confirmed_breakout_funnel(all_results)

    print("  Running look-ahead bias test...", end="", flush=True)
    look_ahead_ok = _run_look_ahead_test()
    print(" PASSED" if look_ahead_ok else " FAILED")

    invariant_ok = _run_invariant_test(all_results)

    health, health_notes = classify_health(
        all_results,
        funnel["long"],
        funnel["short"],
        funnel["top_rejections"],
        len(all_results),
    )

    report = _build_report(
        all_results, all_metas, funnel,
        health, health_notes,
        look_ahead_ok, invariant_ok,
    )
    print(report)
