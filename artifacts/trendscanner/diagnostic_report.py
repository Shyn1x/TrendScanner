"""
diagnostic_report.py
~~~~~~~~~~~~~~~~~~~~
Полная диагностика pipeline — только чтение существующих результатов.

Запуск как скрипт (самостоятельное сканирование):
    python diagnostic_report.py

Запуск из кода (готовые результаты all_results):
    from diagnostic_report import build_diagnostic, print_diagnostic_report
    data = build_diagnostic(all_results, symbols, timeframes)
    print_diagnostic_report(data)

НЕ меняет торговую логику, пороги, веса, Decision Engine или Confidence.
"""

import math
import time
import sys
from collections import Counter
from config   import SYMBOLS, TIMEFRAMES
from settings import TOP_SETUPS_MIN_DECISION_SCORE

# ─────────────────────────────────────────────────────────────────────────────
#  Пороги — ТОЛЬКО чтение из decision_engine / quality_pipeline.
#  Не менять.
# ─────────────────────────────────────────────────────────────────────────────
_CONF_MIN          = 40.0   # _HARD_CONF_MIN из decision_engine.py
_TREND_QUALITY_MIN = 50.0   # _TAKE_TREND_MIN из decision_engine.py
_VOLUME_MIN        = 40.0   # _WATCH_VOLUME_WEAK из decision_engine.py
_STRUCTURE_BAD     = {"TRANSITION_BULLISH", "TRANSITION_BEARISH", "UNKNOWN", ""}


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные функции чтения
# ─────────────────────────────────────────────────────────────────────────────

def _f(value, default=0.0) -> float:
    """Безопасный float, возвращает default при NaN/inf/None."""
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _tf_quality_for_direction(tf_result: dict, direction: str) -> dict:
    """Возвращает quality-блок для конкретного direction или {}."""
    q = tf_result.get("quality", {})
    if not isinstance(q, dict):
        return {}
    d = q.get(direction, {})
    return d if isinstance(d, dict) else {}


def _stage1_data_loaded(tf_result) -> bool:
    if not isinstance(tf_result, dict):
        return False
    if "_error" in tf_result:
        return False
    return tf_result.get("trend", "ERROR") != "ERROR"


def _stage2_trendline(tf_result: dict) -> bool:
    """Хотя бы для одного direction найдена trendline."""
    for d in ("LONG", "SHORT"):
        q = _tf_quality_for_direction(tf_result, d)
        if q.get("line") is not None:
            return True
    return False


def _stage3_breakout_detected(tf_result: dict) -> bool:
    """Хотя бы для одного direction breakout_score > 0."""
    for d in ("LONG", "SHORT"):
        q  = _tf_quality_for_direction(tf_result, d)
        bq = q.get("breakout_quality", {})
        if isinstance(bq, dict) and _f(bq.get("breakout_score")) > 0:
            return True
    return False


def _stage4_breakout_confirmed(tf_result: dict) -> bool:
    """Хотя бы один direction подтверждён (breakout_quality.confirmed OR quality.confirmed)."""
    for d in ("LONG", "SHORT"):
        q  = _tf_quality_for_direction(tf_result, d)
        if q.get("confirmed", False):
            return True
        bq = q.get("breakout_quality", {})
        if isinstance(bq, dict) and bq.get("confirmed", False):
            return True
    return False


def _stage5_trend_quality(tf_result: dict) -> bool:
    """Хотя бы для одного direction trend_quality_score >= _TREND_QUALITY_MIN."""
    for d in ("LONG", "SHORT"):
        q  = _tf_quality_for_direction(tf_result, d)
        tq = q.get("trend_quality", {})
        if isinstance(tq, dict):
            score = _f(tq.get("trend_quality_score", 0))
            if score >= _TREND_QUALITY_MIN:
                return True
    return False


def _stage6_volume(tf_result: dict) -> bool:
    """volume_score >= _VOLUME_MIN (одинаково для обоих directions на этом TF)."""
    for d in ("LONG", "SHORT"):
        q  = _tf_quality_for_direction(tf_result, d)
        vq = q.get("volume_quality", {})
        if isinstance(vq, dict):
            score = _f(vq.get("volume_score", 0))
            if score >= _VOLUME_MIN:
                return True
    return False


def _stage7_market_structure(tf_result: dict) -> bool:
    """Хотя бы один direction с market_structure.structure вне 'плохих' состояний."""
    for d in ("LONG", "SHORT"):
        q  = _tf_quality_for_direction(tf_result, d)
        ms = q.get("market_structure", {})
        if isinstance(ms, dict):
            st = str(ms.get("structure", "")).strip().upper()
            if st and st not in _STRUCTURE_BAD:
                return True
    return False


def _stage8_confidence(tf_result: dict) -> bool:
    """Хотя бы для одного direction confidence >= _CONF_MIN."""
    for d in ("LONG", "SHORT"):
        q    = _tf_quality_for_direction(tf_result, d)
        conf = q.get("confidence", {})
        if isinstance(conf, dict):
            val = _f(conf.get("confidence", 0))
            if val >= _CONF_MIN:
                return True
    # Fallback: top-level confidence (как в analysis.py)
    val = _f(tf_result.get("confidence", 0))
    if val >= _CONF_MIN:
        return True
    return False


def _stage9_decision(tf_result: dict) -> str | None:
    """Возвращает TAKE/WATCH/SKIP или None."""
    dec = str(tf_result.get("decision", "")).upper().strip()
    return dec if dec in ("TAKE", "WATCH", "SKIP") else None


# ─────────────────────────────────────────────────────────────────────────────
#  Сбор rejection reasons
# ─────────────────────────────────────────────────────────────────────────────

def _collect_rejection_reasons(tf_result: dict, stages: dict) -> list[str]:
    """
    Возвращает список строк-причин отказа для одного TF-result.
    Порядок: первая неудавшаяся стадия определяет причину.
    """
    reasons = []

    if not stages["s1"]:
        reasons.append("Data load error")
        return reasons

    if not stages["s2"]:
        reasons.append("No trendline")
        return reasons

    if not stages["s3"]:
        reasons.append("No breakout")
        return reasons

    if not stages["s4"]:
        # Breakout detected but not confirmed → collect breakout reasons
        for d in ("LONG", "SHORT"):
            q  = _tf_quality_for_direction(tf_result, d)
            bq = q.get("breakout_quality", {})
            if isinstance(bq, dict) and _f(bq.get("breakout_score")) > 0:
                r = bq.get("reason", "Breakout not confirmed")
                reasons.append(r[:80] if r else "Breakout not confirmed")
        return reasons if reasons else ["Breakout not confirmed"]

    if not stages["s5"]:
        reasons.append("Weak trend quality")

    if not stages["s6"]:
        reasons.append("Weak volume")

    if not stages["s7"]:
        # Собираем конкретную structure
        for d in ("LONG", "SHORT"):
            q  = _tf_quality_for_direction(tf_result, d)
            ms = q.get("market_structure", {})
            if isinstance(ms, dict):
                st = str(ms.get("structure", "")).strip().upper()
                if st and st not in {"BULLISH", "BEARISH", "RANGE"}:
                    reasons.append(f"Structure {st.lower()}")
        if not reasons:
            reasons.append("Structure not confirmed")

    if not stages["s8"]:
        reasons.append("Low confidence")

    return reasons


def _collect_decision_blockers(tf_result: dict) -> list[str]:
    """
    Извлекает blocker messages из decision_details для обоих directions.
    """
    blockers = []
    dd = tf_result.get("decision_details", {})
    if not isinstance(dd, dict):
        return blockers
    for d in ("LONG", "SHORT"):
        dir_dd = dd.get(d, {})
        if not isinstance(dir_dd, dict):
            continue
        for b in dir_dd.get("blockers", []):
            if isinstance(b, dict):
                msg = b.get("message") or b.get("code") or ""
                if msg:
                    blockers.append(str(msg)[:80])
    return blockers


def _collect_confidence_values(tf_result: dict) -> list[float]:
    """Собирает значения confidence для гистограммы."""
    vals = []
    for d in ("LONG", "SHORT"):
        q    = _tf_quality_for_direction(tf_result, d)
        conf = q.get("confidence", {})
        if isinstance(conf, dict):
            v = _f(conf.get("confidence", -1), -1)
            if v >= 0:
                vals.append(v)
    # Fallback
    top = _f(tf_result.get("confidence", -1), -1)
    if top >= 0 and not vals:
        vals.append(top)
    return vals


# ─────────────────────────────────────────────────────────────────────────────
#  Главная функция сборки данных
# ─────────────────────────────────────────────────────────────────────────────

def build_diagnostic(all_results: dict, symbols: list, timeframes: list) -> dict:
    """
    Принимает all_results (symbol → multi_analysis result),
    возвращает структуру данных диагностики.

    Не запускает никакой аналитики — только читает уже вычисленные поля.
    """
    _ALL_TFS = timeframes  # сохраняем порядок

    total_symbols = len(symbols)
    total_tf_analyses = 0

    # Stage counts — сколько (symbol, TF) прошли данный этап
    s_counts = {f"s{i}": 0 for i in range(1, 10)}

    # Decision counts от FINAL на уровне символа
    dec_counts = {"TAKE": 0, "WATCH": 0, "SKIP": 0}

    # Rejection reasons — первая неудавшаяся стадия
    rejection_counter: Counter = Counter()
    # Decision blockers
    blocker_counter: Counter = Counter()
    # Confidence buckets: 0-20, 20-40, 40-60, 60-80, 80-100
    conf_buckets = [0, 0, 0, 0, 0]

    # По символам
    for sym in symbols:
        sym_result = all_results.get(sym, {})
        if not isinstance(sym_result, dict):
            continue

        # Symbol-level decision (из FINAL)
        final = sym_result.get("FINAL", {})
        if isinstance(final, dict):
            fd = str(final.get("decision", "SKIP")).upper().strip()
            if fd in ("TAKE", "WATCH", "SKIP"):
                dec_counts[fd] += 1
            else:
                dec_counts["SKIP"] += 1
        else:
            dec_counts["SKIP"] += 1

        # По таймфреймам
        for tf in _ALL_TFS:
            tf_result = sym_result.get(tf)
            if tf_result is None:
                continue
            if not isinstance(tf_result, dict):
                continue

            total_tf_analyses += 1

            stages = {
                "s1": _stage1_data_loaded(tf_result),
                "s2": False, "s3": False, "s4": False,
                "s5": False, "s6": False, "s7": False,
                "s8": False, "s9": False,
            }

            if stages["s1"]:
                stages["s2"] = _stage2_trendline(tf_result)
            if stages["s2"]:
                stages["s3"] = _stage3_breakout_detected(tf_result)
            if stages["s3"]:
                stages["s4"] = _stage4_breakout_confirmed(tf_result)
            if stages["s4"]:
                stages["s5"] = _stage5_trend_quality(tf_result)
                stages["s6"] = _stage6_volume(tf_result)
                stages["s7"] = _stage7_market_structure(tf_result)
                stages["s8"] = _stage8_confidence(tf_result)
            if stages["s8"]:
                dec = _stage9_decision(tf_result)
                stages["s9"] = dec is not None

            for k, v in stages.items():
                if v:
                    s_counts[k] += 1

            # Rejection reasons (первая стадия провала)
            reasons = _collect_rejection_reasons(tf_result, stages)
            for r in reasons:
                rejection_counter[r] += 1

            # Decision blockers
            for b in _collect_decision_blockers(tf_result):
                blocker_counter[b] += 1

            # Confidence values
            for cv in _collect_confidence_values(tf_result):
                if cv < 20:
                    conf_buckets[0] += 1
                elif cv < 40:
                    conf_buckets[1] += 1
                elif cv < 60:
                    conf_buckets[2] += 1
                elif cv < 80:
                    conf_buckets[3] += 1
                else:
                    conf_buckets[4] += 1

    return {
        "total_symbols":      total_symbols,
        "total_tf_analyses":  total_tf_analyses,
        "stage_counts":       s_counts,
        "dec_counts":         dec_counts,
        "rejection_counter":  rejection_counter,
        "blocker_counter":    blocker_counter,
        "conf_buckets":       conf_buckets,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Форматирование отчёта
# ─────────────────────────────────────────────────────────────────────────────

def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{count / total * 100:.1f}%"


def format_diagnostic_report(data: dict) -> str:
    """Возвращает форматированный текст отчёта."""
    lines = []
    W = 41  # ширина блока

    def line(s=""):
        lines.append(s)

    total_sym  = data["total_symbols"]
    total_tf   = data["total_tf_analyses"]
    sc         = data["stage_counts"]
    dc         = data["dec_counts"]
    rej        = data["rejection_counter"]
    blk        = data["blocker_counter"]
    cb         = data["conf_buckets"]  # [0-20, 20-40, 40-60, 60-80, 80-100]

    line("=" * W)
    line("PIPELINE DIAGNOSTIC")
    line("=" * W)
    line()
    line(f"Total symbols:            {total_sym}")
    line(f"Total timeframe analyses: {total_tf}")
    line()
    line("-" * W)

    # Stage counts
    stage_labels = [
        ("s1", "Data loaded"),
        ("s2", "Trendline found"),
        ("s3", "Breakout detected"),
        ("s4", "Breakout confirmed"),
        ("s5", "Trend Quality passed"),
        ("s6", "Volume passed"),
        ("s7", "Market Structure passed"),
        ("s8", "Confidence > 40"),
        ("s9", "Decision computed"),
    ]
    for key, label in stage_labels:
        cnt = sc.get(key, 0)
        pct = _pct(cnt, total_tf)
        line(f"{label:<26} {cnt:>4}   ({pct})")

    line()
    line("-" * W)
    line(f"Decision TAKE:   {dc['TAKE']:>4}   ({_pct(dc['TAKE'],  total_sym)})")
    line(f"Decision WATCH:  {dc['WATCH']:>4}   ({_pct(dc['WATCH'], total_sym)})")
    line(f"Decision SKIP:   {dc['SKIP']:>4}   ({_pct(dc['SKIP'],  total_sym)})")

    # ── Rejection reasons ─────────────────────────────────────────────────────
    line()
    line("=" * W)
    line("REJECTED BECAUSE")
    line("=" * W)
    if rej:
        for reason, cnt in rej.most_common(10):
            line(f"  {reason:<35} {cnt:>4}")
    else:
        line("  (no rejections recorded)")

    # ── Decision blockers ─────────────────────────────────────────────────────
    line()
    line("=" * W)
    line("DECISION BLOCKERS")
    line("=" * W)
    if blk:
        for blocker, cnt in blk.most_common(10):
            line(f"  {blocker[:35]:<35} {cnt:>4}")
    else:
        line("  (no blockers recorded)")

    # ── Confidence distribution ───────────────────────────────────────────────
    conf_total = sum(cb)
    line()
    line("=" * W)
    line("CONFIDENCE DISTRIBUTION")
    line("=" * W)
    labels_conf = ["0–20%", "20–40%", "40–60%", "60–80%", "80–100%"]
    for label, cnt in zip(labels_conf, cb):
        bar_len = int(cnt / max(conf_total, 1) * 20)
        bar = "█" * bar_len
        pct = _pct(cnt, conf_total)
        line(f"  {label:<8}  {bar:<20}  {cnt:>4}  ({pct})")

    # ── Pipeline Health Check ─────────────────────────────────────────────────
    line()
    line("=" * W)
    line("PIPELINE HEALTH CHECK")
    line("=" * W)
    line()

    warnings = []
    if total_tf > 0:
        # Вычисляем drop-off на каждом переходе между стадиями
        prev_count = total_tf
        stage_keys_ordered = [k for k, _ in stage_labels]
        stage_names_ordered = [n for _, n in stage_labels]

        for i, (key, label) in enumerate(zip(stage_keys_ordered, stage_names_ordered)):
            cnt = sc.get(key, 0)
            dropped = prev_count - cnt
            if prev_count > 0 and dropped / prev_count >= 0.95:
                warnings.append(
                    f"Possible over-filtering detected at stage {i+1}: "
                    f"{label} (dropped {dropped}/{prev_count}, "
                    f"{_pct(dropped, prev_count)})"
                )
            prev_count = cnt

    if warnings:
        for w in warnings:
            line(f"  ⚠  {w}")
    else:
        line("  Pipeline distribution appears healthy.")

    line()
    line("=" * W)

    return "\n".join(lines)


def print_diagnostic_report(data: dict):
    """Выводит отчёт в stdout."""
    print(format_diagnostic_report(data))


# ─────────────────────────────────────────────────────────────────────────────
#  Запуск как скрипт
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from multi_tf import multi_analysis

    symbols   = SYMBOLS
    tfs       = TIMEFRAMES
    all_res   = {}

    total = len(symbols)
    for i, sym in enumerate(symbols, 1):
        sys.stdout.write(f"\rScanning {sym} ({i}/{total})...")
        sys.stdout.flush()
        try:
            all_res[sym] = multi_analysis(sym)
        except Exception as e:
            all_res[sym] = {"_error": str(e), "FINAL": {}}
        time.sleep(0.05)

    sys.stdout.write("\r" + " " * 40 + "\r")  # clear progress line
    sys.stdout.flush()

    data = build_diagnostic(all_res, symbols, tfs)
    print_diagnostic_report(data)
