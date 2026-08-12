"""
historical_replay.py
~~~~~~~~~~~~~~~~~~~~
Historical Pipeline Replay — диагностика без изменения торговой логики.

Публичный интерфейс:
    replay_timeframe(df, symbol, timeframe, warmup_bars, max_replay_bars) -> dict
    build_confirmed_breakout_funnel(replay_results) -> dict
    format_funnel_report(funnel) -> str

НЕ меняет и НЕ дублирует торговую логику.
Единственная точка входа в pipeline — analyze_timeframe(historical_df).
"""

import math
from analysis import analyze_timeframe

# ─────────────────────────────────────────────────────────────────────────────
#  Пороги — только чтение (те же что в decision_engine.py)
# ─────────────────────────────────────────────────────────────────────────────
_CONF_40   = 40.0
_CONF_50   = 50.0
_TREND_MIN = 50.0
_VOL_MIN   = 40.0


# ─────────────────────────────────────────────────────────────────────────────
#  Вспомогательные читалки
# ─────────────────────────────────────────────────────────────────────────────

def _f(v, default=0.0) -> float:
    try:
        r = float(v)
        return r if math.isfinite(r) else default
    except (TypeError, ValueError):
        return default


def _b(v) -> bool:
    return bool(v) if v is not None else False


def _s(v, default="") -> str:
    return str(v).strip() if v is not None else default


def _ready_quality_direction(result: dict, direction: str) -> dict:
    quality_root = result.get("quality", {})
    quality_root = quality_root if isinstance(quality_root, dict) else {}
    direction_quality = quality_root.get(direction, {})
    direction_quality = direction_quality if isinstance(direction_quality, dict) else {}

    breakout_quality = direction_quality.get("breakout_quality", {})
    breakout_quality = breakout_quality if isinstance(breakout_quality, dict) else {}

    trend_quality = direction_quality.get("trend_quality", {})
    trend_quality = trend_quality if isinstance(trend_quality, dict) else {}

    volume_quality = direction_quality.get("volume_quality", {})
    volume_quality = volume_quality if isinstance(volume_quality, dict) else {}

    structure_quality_raw = direction_quality.get("structure_quality")
    if isinstance(structure_quality_raw, dict):
        structure_quality_score = _f(
            structure_quality_raw.get("structure_score", structure_quality_raw.get("score")),
            None,
        )
    elif structure_quality_raw is not None:
        structure_quality_score = _f(structure_quality_raw, None)
    else:
        structure_quality_score = None

    market_structure = direction_quality.get("market_structure", {})
    market_structure = market_structure if isinstance(market_structure, dict) else {}

    confidence = direction_quality.get("confidence", {})
    confidence = confidence if isinstance(confidence, dict) else {}

    return {
        "confirmed": _b(direction_quality.get("confirmed", False)),
        "signal": _s(direction_quality.get("signal", "")) or _s(result.get("signal", "WAIT")),
        "confidence": {
            "confidence": _f(confidence.get("confidence"), None),
        },
        "trend_quality": {
            "trend_quality_score": _f(trend_quality.get("trend_quality_score"), None),
        },
        "volume_quality": {
            "volume_score": _f(volume_quality.get("volume_score"), None),
        },
        "breakout_quality": {
            "confirmed": _b(breakout_quality.get("confirmed", False)),
            "breakout_score": _f(breakout_quality.get("breakout_score"), None),
        },
        "structure_quality": {
            "structure_score": structure_quality_score,
        },
        "market_structure": {
            "structure": _s(market_structure.get("structure", "")),
        },
    }


def _ready_decision_direction(result: dict, direction: str) -> dict:
    decision_root = result.get("decision_details", {})
    decision_root = decision_root if isinstance(decision_root, dict) else {}
    direction_decision = decision_root.get(direction, {})
    direction_decision = direction_decision if isinstance(direction_decision, dict) else {}

    component_scores = direction_decision.get("component_scores", {})
    component_scores = component_scores if isinstance(component_scores, dict) else {}

    market_context = direction_decision.get("market_context", {})
    market_context = market_context if isinstance(market_context, dict) else {}

    blockers_raw = direction_decision.get("blockers", [])
    blockers: list = []
    if isinstance(blockers_raw, list):
        for blocker in blockers_raw:
            if isinstance(blocker, dict):
                blockers.append({
                    "code": _s(blocker.get("code", "")),
                    "message": _s(blocker.get("message", "")),
                })
            elif blocker is not None:
                blockers.append(_s(blocker, ""))

    return {
        "decision": _s(direction_decision.get("decision", "SKIP")).upper(),
        "decision_score": _f(direction_decision.get("decision_score"), None),
        "breakout_confirmed": _b(direction_decision.get("breakout_confirmed", False)),
        "blockers": blockers,
        "component_scores": {
            "trend_quality": _f(component_scores.get("trend_quality"), None),
            "volume_quality": _f(component_scores.get("volume_quality"), None),
            "breakout_quality": _f(component_scores.get("breakout_quality"), None),
            "structure_quality": _f(component_scores.get("structure_quality"), None),
        },
        "market_context": {
            "alignment": _s(market_context.get("alignment", "UNKNOWN")).upper() or "UNKNOWN",
        },
        "signal": _s(direction_decision.get("signal", "")) or _s(result.get("signal", "WAIT")),
        "confidence": _f(direction_decision.get("confidence"), None),
    }


def _extract_timeframe_result_for_ready(result: dict) -> dict:
    return {
        "quality": {
            "LONG": _ready_quality_direction(result, "LONG"),
            "SHORT": _ready_quality_direction(result, "SHORT"),
        },
        "decision_details": {
            "LONG": _ready_decision_direction(result, "LONG"),
            "SHORT": _ready_decision_direction(result, "SHORT"),
        },
    }


def _extract_dir_data(result: dict, direction: str) -> dict:
    """
    Извлекает компактные данные для одного направления из результата
    analyze_timeframe. Не хранит сырые DataFrame или большие объекты.
    """
    q = result.get("quality", {})
    if not isinstance(q, dict):
        q = {}
    qd = q.get(direction, {})
    if not isinstance(qd, dict):
        qd = {}

    # breakout
    bq = qd.get("breakout_quality", {})
    bq = bq if isinstance(bq, dict) else {}
    breakout_detected   = _f(bq.get("breakout_score", 0)) > 0
    breakout_confirmed  = _b(qd.get("confirmed", False)) or _b(bq.get("confirmed", False))
    breakout_score      = _f(bq.get("breakout_score"), None)   # None if missing

    # trend quality
    tq = qd.get("trend_quality", {})
    tq = tq if isinstance(tq, dict) else {}
    trend_quality = _f(tq.get("trend_quality_score"), None)

    # volume quality (shared per TF, stored under each direction)
    vq = qd.get("volume_quality", {})
    vq = vq if isinstance(vq, dict) else {}
    volume_quality = _f(vq.get("volume_score"), None)

    # structure quality
    sq = qd.get("structure_quality")
    if isinstance(sq, dict):
        structure_quality = _f(sq.get("structure_score", sq.get("score")), None)
    elif sq is not None:
        structure_quality = _f(sq, None)
    else:
        structure_quality = None

    # market structure state
    ms = qd.get("market_structure", {})
    ms = ms if isinstance(ms, dict) else {}
    structure_state = _s(ms.get("structure", ""))

    # confidence
    conf_d = qd.get("confidence", {})
    conf_d = conf_d if isinstance(conf_d, dict) else {}
    confidence = _f(conf_d.get("confidence"), None)

    # per-direction decision
    dd = result.get("decision_details", {})
    dd = dd if isinstance(dd, dict) else {}
    dir_dec = dd.get(direction, {})
    dir_dec = dir_dec if isinstance(dir_dec, dict) else {}
    dir_decision = _s(dir_dec.get("decision", "SKIP")).upper()

    # blocker codes
    blockers = dir_dec.get("blockers", [])
    blocker_codes = []
    if isinstance(blockers, list):
        for b in blockers:
            if isinstance(b, dict):
                code = _s(b.get("code") or b.get("message") or "")
                if code:
                    blocker_codes.append(code[:80])

    return {
        "breakout_detected":  breakout_detected,
        "breakout_confirmed": breakout_confirmed,
        "breakout_score":     breakout_score,
        "trend_quality":      trend_quality,
        "volume_quality":     volume_quality,
        "structure_quality":  structure_quality,
        "structure_state":    structure_state,
        "confidence":         confidence,
        "decision":           dir_decision,
        "blocker_codes":      blocker_codes,
    }


def _signal_timestamp(df, end_index: int):
    """
    Временна́я метка сигнальной свечи (df.iloc[-2] в историческом срезе).
    Это df.iloc[end_index - 1].
    """
    try:
        ts = df.iloc[end_index - 1].get("time")
        if ts is not None:
            return int(ts)
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Основная функция
# ─────────────────────────────────────────────────────────────────────────────

def replay_timeframe(
    df,
    symbol: str,
    timeframe: str,
    warmup_bars: int = 120,
    max_replay_bars: int = 200,
) -> dict:
    """
    Replay pipeline по закрытым историческим свечам.

    Для каждой точки:
        historical_df = df.iloc[:end_index + 1].copy()
        result = analyze_timeframe(historical_df)

    Сигнальная свеча находится на позиции historical_df.iloc[-2]
    (= df.iloc[end_index - 1]).

    Параметры
    ---------
    df              : полный DataFrame (все доступные свечи)
    symbol          : название символа (для метаданных)
    timeframe       : название таймфрейма (для метаданных)
    warmup_bars     : минимальное кол-во баров до сигнальной свечи
    max_replay_bars : максимум replay-шагов

    Возвращает
    ----------
    {
        "replay_results": [list of step dicts],
        "meta": {
            "symbol":        str,
            "timeframe":     str,
            "warmup_bars":   int,
            "total_bars":    int,
            "replay_count":  int,
            "skipped":       int,
            "insufficient_data": bool,
        }
    }
    """
    import pandas as pd

    meta_base = {
        "symbol":            symbol,
        "timeframe":         timeframe,
        "warmup_bars":       warmup_bars,
        "total_bars":        0,
        "replay_count":      0,
        "skipped":           0,
        "insufficient_data": False,
    }

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        meta_base["insufficient_data"] = True
        return {"replay_results": [], "meta": meta_base}

    n = len(df)
    meta_base["total_bars"] = n

    # Нужно минимум: warmup_bars строк до сигнала + сигнальная + текущая открытая
    # end_index = warmup_bars (первый шаг)
    # historical_df = df.iloc[:warmup_bars + 1]  → warmup_bars+1 строк
    # signal_candle = df.iloc[warmup_bars - 1] = historical_df.iloc[-2]
    if n < warmup_bars + 2:
        meta_base["insufficient_data"] = True
        return {"replay_results": [], "meta": meta_base}

    # Сколько шагов можно сделать
    # end_index идёт от warmup_bars до min(n-1, warmup_bars + max_replay_bars - 1)
    actual_steps = min(max_replay_bars, n - warmup_bars)

    replay_results = []
    skipped = 0

    # df_frozen: оригинал не меняем. Делаем одну копию для индексации.
    df_frozen = df.reset_index(drop=True)  # гарантируем 0-based int index

    for step in range(actual_steps):
        end_index = warmup_bars + step          # последний включаемый индекс

        # Слайс — only past data, no look-ahead
        historical_df = df_frozen.iloc[:end_index + 1].copy()

        try:
            result = analyze_timeframe(historical_df)
        except Exception as exc:
            skipped += 1
            continue

        if not isinstance(result, dict) or result.get("trend") == "ERROR":
            skipped += 1
            continue

        signal_ts = _signal_timestamp(df_frozen, end_index)

        # Top-level fields
        signal   = _s(result.get("signal",             "WAIT")).upper()
        conf_top = _f(result.get("confidence",         0.0))
        decision = _s(result.get("decision",           "SKIP")).upper()
        dec_dir  = _s(result.get("decision_direction", "NONE")).upper()
        dec_score= _f(result.get("decision_score",     0.0))
        dec_reason = _s(result.get("decision_reason",  ""))

        long_data  = _extract_dir_data(result, "LONG")
        short_data = _extract_dir_data(result, "SHORT")
        timeframe_result = _extract_timeframe_result_for_ready(result)

        replay_results.append({
            "symbol":             symbol,
            "timeframe":          timeframe,
            "replay_index":       step,
            "signal_timestamp":   signal_ts,
            "signal":             signal,
            "confidence":         conf_top,
            "decision":           decision,
            "decision_direction": dec_dir,
            "decision_score":     dec_score,
            "decision_reason":    dec_reason,
            "long":               long_data,
            "short":              short_data,
            "timeframe_result":   timeframe_result,
        })

    meta_base["replay_count"] = len(replay_results)
    meta_base["skipped"]      = skipped

    return {"replay_results": replay_results, "meta": meta_base}


# ─────────────────────────────────────────────────────────────────────────────
#  Primary rejection reason (строго по приоритету из спецификации)
# ─────────────────────────────────────────────────────────────────────────────

def _primary_rejection_reason(entry: dict, direction: str) -> str:
    """
    Возвращает ОДНУ главную причину отказа по приоритету:

    1. invalid pipeline data
    2. breakout not confirmed
    3. strong structure opposition
    4. confidence below 40
    5. confidence 40–49
    6. weak breakout score
    7. weak trend quality
    8. weak volume
    9. transitional/range structure
    10. signal remained WAIT
    11. other
    """
    d = entry.get(direction.lower(), {})
    if not isinstance(d, dict):
        return "invalid pipeline data"

    # 1. invalid
    if d.get("breakout_score") is None and d.get("confidence") is None:
        return "invalid pipeline data"

    # 2. breakout not confirmed
    if not d.get("breakout_confirmed", False):
        return "breakout not confirmed"

    # 3. strong structure opposition (blocker code)
    codes = [c.upper() for c in d.get("blocker_codes", [])]
    if any("OPPOSITION" in c or "STRUCT" in c for c in codes):
        return "strong structure opposition"

    # 4. confidence < 40
    conf = d.get("confidence")
    if conf is None or _f(conf, -1) < _CONF_40:
        return "confidence below 40"

    # 5. confidence 40–49
    conf_v = _f(conf, 0)
    if conf_v < _CONF_50:
        return "confidence 40-49"

    # 6. weak breakout score
    bs = _f(d.get("breakout_score", 0))
    if bs < 30.0:
        return "weak breakout score"

    # 7. weak trend quality
    tq = d.get("trend_quality")
    if tq is not None and _f(tq, 0) < _TREND_MIN:
        return "weak trend quality"

    # 8. weak volume
    vq = d.get("volume_quality")
    if vq is not None and _f(vq, 0) < _VOL_MIN:
        return "weak volume"

    # 9. transitional/range structure
    st = d.get("structure_state", "")
    if "TRANSITION" in st.upper() or st.upper() == "RANGE":
        return "transitional/range structure"

    # 10. signal remained WAIT
    if entry.get("signal", "WAIT") == "WAIT":
        return "signal remained WAIT"

    return "other"


# ─────────────────────────────────────────────────────────────────────────────
#  Воронка подтверждённых пробоев
# ─────────────────────────────────────────────────────────────────────────────

def build_confirmed_breakout_funnel(replay_results: list) -> dict:
    """
    Строит воронку для LONG и SHORT отдельно.

    Возвращает
    ----------
    {
        "total":  int,          # все replay-точки
        "long":   {funnel dict},
        "short":  {funnel dict},
        "top_rejections": Counter,   # primary_rejection_reason: count
    }

    funnel dict (на каждое направление):
        total_points, breakout_detected, breakout_confirmed,
        trend_quality_ok, volume_ok, structure_available,
        confidence_ge_40, confidence_ge_50,
        decision_watch, decision_take, decision_skip,
        top_rejections: Counter
    """
    from collections import Counter

    def empty_funnel():
        return {
            "total_points":       0,
            "breakout_detected":  0,
            "breakout_confirmed": 0,
            "trend_quality_ok":   0,
            "volume_ok":          0,
            "structure_available":0,
            "confidence_ge_40":   0,
            "confidence_ge_50":   0,
            "decision_watch":     0,
            "decision_take":      0,
            "decision_skip":      0,
            "top_rejections":     Counter(),
        }

    long_f  = empty_funnel()
    short_f = empty_funnel()
    global_rej: Counter = Counter()

    for entry in replay_results:
        if not isinstance(entry, dict):
            continue

        for direction, funnel in (("long", long_f), ("short", short_f)):
            d = entry.get(direction, {})
            if not isinstance(d, dict):
                continue

            funnel["total_points"] += 1

            if d.get("breakout_detected", False):
                funnel["breakout_detected"] += 1

            if d.get("breakout_confirmed", False):
                funnel["breakout_confirmed"] += 1

                tq = d.get("trend_quality")
                if tq is not None and _f(tq, 0) >= _TREND_MIN:
                    funnel["trend_quality_ok"] += 1

                vq = d.get("volume_quality")
                if vq is not None and _f(vq, 0) >= _VOL_MIN:
                    funnel["volume_ok"] += 1

                st = d.get("structure_state", "")
                if st and st.upper() not in ("", "UNKNOWN"):
                    funnel["structure_available"] += 1

                conf = d.get("confidence")
                conf_v = _f(conf, -1) if conf is not None else -1
                if conf_v >= _CONF_40:
                    funnel["confidence_ge_40"] += 1
                if conf_v >= _CONF_50:
                    funnel["confidence_ge_50"] += 1

                dec = d.get("decision", "SKIP")
                if dec == "TAKE":
                    funnel["decision_take"] += 1
                elif dec == "WATCH":
                    funnel["decision_watch"] += 1
                else:
                    funnel["decision_skip"] += 1

                    # primary rejection reason only for confirmed breakouts that ended SKIP
                    reason = _primary_rejection_reason(entry, direction)
                    funnel["top_rejections"][reason] += 1
                    global_rej[reason] += 1

    return {
        "total":          len(replay_results),
        "long":           long_f,
        "short":          short_f,
        "top_rejections": global_rej,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Форматирование воронки
# ─────────────────────────────────────────────────────────────────────────────

def format_funnel_report(funnel: dict) -> str:
    lines = []

    def _row(label, val, total=None):
        pct = f"  ({val/total*100:.1f}%)" if total else ""
        lines.append(f"  {label:<28} {val:>4}{pct}")

    for direction, key in (("LONG", "long"), ("SHORT", "short")):
        f = funnel[key]
        tp = f["total_points"]
        bc = f["breakout_confirmed"]
        lines.append(f"\n{direction}:")
        _row("Total points",          tp)
        _row("Breakout detected",     f["breakout_detected"],  tp)
        _row("Breakout confirmed",    bc,                      tp)
        if bc > 0:
            _row("Trend quality >= 50",  f["trend_quality_ok"],   bc)
            _row("Volume >= 40",         f["volume_ok"],           bc)
            _row("Structure available",  f["structure_available"], bc)
            _row("Confidence >= 40",     f["confidence_ge_40"],    bc)
            _row("Confidence >= 50",     f["confidence_ge_50"],    bc)
            _row("Decision WATCH",       f["decision_watch"],      bc)
            _row("Decision TAKE",        f["decision_take"],       bc)
            _row("Decision SKIP",        f["decision_skip"],       bc)
        if f["top_rejections"]:
            lines.append("  Confirmed breakouts rejected:")
            for reason, cnt in f["top_rejections"].most_common(5):
                lines.append(f"    {reason:<30} {cnt}")

    return "\n".join(lines)
