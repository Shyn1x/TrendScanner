"""
decision_engine.py
~~~~~~~~~~~~~~~~~~
Trade Decision Engine — интерпретирует готовый результат quality_pipeline.py
и возвращает одно из трёх аналитических решений:

    TAKE  — сетап соответствует всем критериям качества
    WATCH — сетап требует дополнительного подтверждения
    SKIP  — сетап не прошёл минимальные требования

Модуль НЕ выполняет:
    • поиск пивотов, трендовых линий, объёма или структуры;
    • расчёт ATR, Entry/Stop/TP, размера позиции;
    • подключение к Streamlit, analysis.py, multi_tf.py или confidence.py.

Публичный API:
    evaluate_trade_decision(quality_result, direction) → dict
    evaluate_both_directions(quality_analysis)         → dict
"""

from __future__ import annotations
import math
from typing import Any


# ─── константы ────────────────────────────────────────────────────────────────

# TAKE-порог confidence
_TAKE_CONF_THRESHOLD  = 70.0
# WATCH-порог confidence (нижняя граница)
_WATCH_CONF_THRESHOLD = 50.0
# Hard-blocker: минимальный confidence для любого активного решения
_HARD_CONF_MIN        = 40.0

# TAKE: минимальные мягкие критерии
_TAKE_BREAKOUT_MIN    = 60.0
_TAKE_TREND_MIN       = 50.0
_MIN_COMPONENTS_TAKE  = 3

# WATCH: мягкий порог volume
_WATCH_VOLUME_WEAK    = 40.0

# Blocker: порог структурной оппозиции
_STRONG_OPPOSITION_SCORE = 25.0

# Суммарные коды blockers
_BLOCKER_NO_DATA             = "NO_DATA"
_BLOCKER_BREAKOUT            = "BREAKOUT_NOT_CONFIRMED"
_BLOCKER_CONF_LOW            = "CONFIDENCE_TOO_LOW"
_BLOCKER_SIGNAL_MISMATCH     = "SIGNAL_DIRECTION_MISMATCH"
_BLOCKER_STRONG_OPPOSITION   = "STRONG_STRUCTURE_OPPOSITION"
_BLOCKER_INVALID_METRICS     = "INVALID_METRICS"
_BLOCKER_PIPELINE_ERROR      = "PIPELINE_ERROR"

_VALID_DECISIONS = {"TAKE", "WATCH", "SKIP"}
_VALID_DIRECTIONS = {"LONG", "SHORT"}


# ─── безопасные геттеры ───────────────────────────────────────────────────────

def _safe_float(value: Any, default: float | None = None) -> float | None:
    """Приводит к float или возвращает default при ошибке/NaN/inf."""
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _safe_str(value: Any, default: str = "UNKNOWN") -> str:
    if value is None:
        return default
    return str(value).strip().upper() or default


def _extract_fields(quality_result: dict) -> dict:
    """
    Безопасно извлекает все нужные поля из качества pipeline.
    Возвращает dict с гарантированными ключами; None при отсутствии данных.
    """
    # top-level
    signal        = _safe_str(quality_result.get("signal"),    "UNKNOWN")
    confirmed     = bool(quality_result.get("confirmed", False))
    pipeline_reason = str(quality_result.get("reason", ""))

    # confidence
    conf_dict  = quality_result.get("confidence") or {}
    confidence = _safe_float(conf_dict.get("confidence") if isinstance(conf_dict, dict) else None)
    conf_label = _safe_str(
        conf_dict.get("label") if isinstance(conf_dict, dict) else None,
        "UNKNOWN"
    )
    avail_comps = int(conf_dict.get("available_components", 0)) if isinstance(conf_dict, dict) else 0

    # trend_quality
    tq_dict    = quality_result.get("trend_quality") or {}
    tq_score   = _safe_float(tq_dict.get("trend_quality_score") if isinstance(tq_dict, dict) else None)

    # volume_quality
    vq_dict    = quality_result.get("volume_quality") or {}
    vq_score   = _safe_float(vq_dict.get("volume_score") if isinstance(vq_dict, dict) else None)

    # breakout_quality
    bq_dict       = quality_result.get("breakout_quality") or {}
    bq_score      = _safe_float(bq_dict.get("breakout_score") if isinstance(bq_dict, dict) else None)
    bq_confirmed  = bool(bq_dict.get("confirmed", False)) if isinstance(bq_dict, dict) else False

    # structure_quality
    sq_dict       = quality_result.get("structure_quality") or {}
    sq_score      = _safe_float(sq_dict.get("structure_score") if isinstance(sq_dict, dict) else None)
    sq_alignment  = _safe_str(
        sq_dict.get("alignment") if isinstance(sq_dict, dict) else None,
        "UNKNOWN"
    )
    sq_available  = bool(sq_dict.get("available", False)) if isinstance(sq_dict, dict) else False

    # market_structure
    ms_dict     = quality_result.get("market_structure") or {}
    ms_structure = _safe_str(
        ms_dict.get("structure") if isinstance(ms_dict, dict) else None,
        "UNKNOWN"
    )
    bos_dict    = (ms_dict.get("bos") or {}) if isinstance(ms_dict, dict) else {}
    bos_dir     = _safe_str(
        bos_dict.get("direction") if isinstance(bos_dict, dict) else None,
        "NONE"
    )
    bos_confirmed_ms = bool(bos_dict.get("confirmed", False)) if isinstance(bos_dict, dict) else False
    choch_raw   = _safe_str(
        ms_dict.get("choch") if isinstance(ms_dict, dict) else None,
        "NONE"
    )
    choch = choch_raw if choch_raw in ("BULLISH", "BEARISH") else "NONE"

    return {
        "signal":         signal,
        "confirmed":      confirmed,
        "pipeline_reason": pipeline_reason,
        "confidence":     confidence,
        "conf_label":     conf_label,
        "avail_comps":    avail_comps,
        "tq_score":       tq_score,
        "vq_score":       vq_score,
        "bq_score":       bq_score,
        "bq_confirmed":   bq_confirmed,
        "sq_score":       sq_score,
        "sq_alignment":   sq_alignment,
        "sq_available":   sq_available,
        "ms_structure":   ms_structure,
        "bos_dir":        bos_dir,
        "bos_confirmed_ms": bos_confirmed_ms,
        "choch":          choch,
    }


# ─── вычисление decision_score ────────────────────────────────────────────────

def _calc_decision_score(fields: dict, direction: str) -> float:
    """
    decision_score = confidence + поправки.
    Результат ограничен [0, 100].
    """
    base = fields["confidence"] if fields["confidence"] is not None else 0.0
    score = base

    bq_score     = fields["bq_score"]
    tq_score     = fields["tq_score"]
    vq_score     = fields["vq_score"]
    alignment    = fields["sq_alignment"]
    bos_dir      = fields["bos_dir"]
    bos_confirmed= fields["bos_confirmed_ms"]
    choch        = fields["choch"]
    ms_structure = fields["ms_structure"]

    # ── бонусы ────────────────────────────────────────────────────────────────
    if bq_score is not None and bq_score >= 80:
        score += 5
    if tq_score is not None and tq_score >= 75:
        score += 5
    if vq_score is not None and vq_score >= 80:
        score += 5
    if alignment == "ALIGNED":
        score += 5
    # BOS совпадает с направлением
    if bos_confirmed:
        bos_aligned = (
            (direction == "LONG"  and bos_dir == "BULLISH") or
            (direction == "SHORT" and bos_dir == "BEARISH")
        )
        if bos_aligned:
            score += 5

    # ── штрафы ────────────────────────────────────────────────────────────────
    if vq_score is not None and vq_score < 40:
        score -= 10
    if alignment == "TRANSITION":
        score -= 10
    if ms_structure == "RANGE":
        score -= 15

    # CHoCH против направления
    if choch != "NONE":
        choch_opposed = (
            (direction == "LONG"  and choch == "BEARISH") or
            (direction == "SHORT" and choch == "BULLISH")
        )
        if choch_opposed:
            score -= 20

    if alignment == "OPPOSED":
        score -= 25

    return float(max(0.0, min(100.0, score)))


# ─── сборка факторов и blockers ───────────────────────────────────────────────

def _collect_factors(
    fields: dict,
    direction: str,
    decision_score: float,
) -> tuple[list[str], list[str], list[dict]]:
    """
    Возвращает (positive_factors, warning_factors, blockers).
    """
    pos: list[str] = []
    warn: list[str] = []
    blk: list[dict] = []

    conf        = fields["confidence"]
    tq_score    = fields["tq_score"]
    vq_score    = fields["vq_score"]
    bq_score    = fields["bq_score"]
    sq_score    = fields["sq_score"]
    alignment   = fields["sq_alignment"]
    bos_dir     = fields["bos_dir"]
    bos_conf    = fields["bos_confirmed_ms"]
    choch       = fields["choch"]
    ms_struct   = fields["ms_structure"]
    signal      = fields["signal"]
    avail       = fields["avail_comps"]
    sq_avail    = fields["sq_available"]

    # ── positive_factors ──────────────────────────────────────────────────────
    if fields["bq_confirmed"]:
        pos.append("Breakout confirmed")
    if conf is not None:
        if conf >= 85:
            pos.append("Very high confidence")
        elif conf >= 70:
            pos.append("High confidence")
        elif conf >= 50:
            pos.append("Sufficient confidence")
    if tq_score is not None and tq_score >= 75:
        pos.append("Trendline quality is strong")
    elif tq_score is not None and tq_score >= 50:
        pos.append("Trendline quality is adequate")
    if vq_score is not None and vq_score >= 80:
        pos.append("Volume confirms breakout")
    elif vq_score is not None and vq_score >= 60:
        pos.append("Volume moderately confirms breakout")
    if bq_score is not None and bq_score >= 80:
        pos.append("Strong breakout score")
    elif bq_score is not None and bq_score >= 60:
        pos.append("Adequate breakout score")
    if sq_avail and alignment == "ALIGNED":
        pos.append(f"Market structure aligned with {direction}")
    if bos_conf:
        bos_aligned = (
            (direction == "LONG"  and bos_dir == "BULLISH") or
            (direction == "SHORT" and bos_dir == "BEARISH")
        )
        if bos_aligned:
            pos.append(f"BOS aligned with {direction}")
    if signal == direction:
        pos.append(f"Pipeline signal matches {direction}")

    # ── warning_factors ───────────────────────────────────────────────────────
    if vq_score is not None and vq_score < 40:
        warn.append("Low volume confirmation")
    elif not sq_avail and vq_score is None:
        warn.append("Volume data unavailable")
    if alignment in ("TRANSITION", "TRANSITION_BULLISH", "TRANSITION_BEARISH"):
        warn.append("Market structure is transitional")
    if ms_struct == "RANGE":
        warn.append("Market is ranging")
    if signal == "WAIT":
        warn.append("Pipeline signal is WAIT")
    if avail < 4:
        warn.append(f"Only {avail} of 4 quality components available")
    if choch != "NONE":
        choch_opposed = (
            (direction == "LONG"  and choch == "BEARISH") or
            (direction == "SHORT" and choch == "BULLISH")
        )
        if choch_opposed:
            warn.append(f"CHoCH {choch} opposes {direction}")
    if tq_score is not None and tq_score < 50:
        warn.append("Trendline quality is weak")
    if bq_score is not None and bq_score < 60:
        warn.append("Breakout score is below threshold")

    # ── blockers ──────────────────────────────────────────────────────────────
    # (заполняются снаружи; здесь пусто — blockers передаются отдельно)

    return pos, warn, blk


def _add_blocker(
    blockers: list[dict],
    code: str,
    message: str,
) -> None:
    blockers.append({"code": code, "message": message})


# ─── основная функция ─────────────────────────────────────────────────────────

def evaluate_trade_decision(
    quality_result: dict | None,
    direction: str,
) -> dict:
    """
    Интерпретирует результат analyze_quality(df, direction) из quality_pipeline.py
    и возвращает аналитическое торговое решение.

    Параметры:
        quality_result — dict от analyze_quality() или None.
        direction      — "LONG" или "SHORT".

    Возвращает:
        {
            "direction":           "LONG" | "SHORT",
            "decision":            "TAKE" | "WATCH" | "SKIP",
            "decision_score":      float,
            "confidence":          float | None,
            "confidence_label":    str,
            "signal":              str,
            "breakout_confirmed":  bool,
            "available_components": int,
            "positive_factors":    [str],
            "warning_factors":     [str],
            "blockers":            [{"code": str, "message": str}],
            "component_scores": {
                "trend_quality":    float | None,
                "volume_quality":   float | None,
                "breakout_quality": float | None,
                "structure_quality": float | None,
            },
            "market_context": {
                "structure":    str,
                "alignment":    str,
                "bos_direction": str,
                "choch":        str,
            },
            "summary": str,
            "reason":  str,
        }

    Исключения:
        ValueError — если direction не "LONG" и не "SHORT".
    """
    # ── валидация direction ────────────────────────────────────────────────────
    if not isinstance(direction, str) or direction.upper() not in _VALID_DIRECTIONS:
        raise ValueError(
            f"direction должен быть 'LONG' или 'SHORT', получено: {direction!r}"
        )
    direction = direction.upper()

    blockers: list[dict] = []

    # ── hard blocker: нет данных ──────────────────────────────────────────────
    if quality_result is None or not isinstance(quality_result, dict):
        _add_blocker(blockers, _BLOCKER_NO_DATA,
                     "quality_result is None or not a dict")
        return _build_result(
            direction=direction,
            decision="SKIP",
            decision_score=0.0,
            confidence=None,
            conf_label="UNKNOWN",
            signal="UNKNOWN",
            bq_confirmed=False,
            avail_comps=0,
            pos=[], warn=[],
            blockers=blockers,
            comp_scores={"trend_quality": None, "volume_quality": None,
                         "breakout_quality": None, "structure_quality": None},
            market_ctx={"structure": "UNKNOWN", "alignment": "UNKNOWN",
                        "bos_direction": "NONE", "choch": "NONE"},
            reason="No quality data available.",
        )

    # ── извлечение полей ──────────────────────────────────────────────────────
    try:
        f = _extract_fields(quality_result)
    except Exception as exc:
        _add_blocker(blockers, _BLOCKER_PIPELINE_ERROR,
                     f"Failed to extract pipeline fields: {exc}")
        return _build_result(
            direction=direction,
            decision="SKIP",
            decision_score=0.0,
            confidence=None,
            conf_label="UNKNOWN",
            signal="UNKNOWN",
            bq_confirmed=False,
            avail_comps=0,
            pos=[], warn=[],
            blockers=blockers,
            comp_scores={"trend_quality": None, "volume_quality": None,
                         "breakout_quality": None, "structure_quality": None},
            market_ctx={"structure": "UNKNOWN", "alignment": "UNKNOWN",
                        "bos_direction": "NONE", "choch": "NONE"},
            reason="Internal pipeline error during field extraction.",
        )

    # ── проверка метрик ───────────────────────────────────────────────────────
    conf = f["confidence"]
    if conf is None:
        _add_blocker(blockers, _BLOCKER_INVALID_METRICS,
                     "Confidence is missing or not a finite number")

    # ── сборка decision_score ─────────────────────────────────────────────────
    decision_score = _calc_decision_score(f, direction)

    # ── hard blockers ─────────────────────────────────────────────────────────
    if not f["bq_confirmed"]:
        _add_blocker(blockers, _BLOCKER_BREAKOUT,
                     "Breakout is not confirmed")

    if conf is not None and conf < _HARD_CONF_MIN:
        _add_blocker(blockers, _BLOCKER_CONF_LOW,
                     f"Confidence {conf:.1f} is below minimum threshold {_HARD_CONF_MIN}")

    # Signal direction mismatch: signal в другую сторону (не direction и не WAIT)
    sig = f["signal"]
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if sig == opposite:
        _add_blocker(blockers, _BLOCKER_SIGNAL_MISMATCH,
                     f"Pipeline signal '{sig}' does not match direction '{direction}'")

    # Strong structure opposition
    sq_alignment = f["sq_alignment"]
    sq_score_val = f["sq_score"]
    if (sq_alignment == "OPPOSED"
            and sq_score_val is not None
            and sq_score_val < _STRONG_OPPOSITION_SCORE):
        _add_blocker(blockers, _BLOCKER_STRONG_OPPOSITION,
                     f"Strong market-structure opposition "
                     f"(alignment=OPPOSED, structure_score={sq_score_val:.1f})")

    # ── positive и warning факторы ────────────────────────────────────────────
    pos, warn, _ = _collect_factors(f, direction, decision_score)

    # ── определение решения ───────────────────────────────────────────────────
    if blockers:
        decision = "SKIP"
        reason = _build_skip_reason(blockers, f)
    elif conf is None:
        decision = "SKIP"
        reason = "Confidence data unavailable."
    elif conf < _WATCH_CONF_THRESHOLD:
        # confidence 40–49.99: нет hard blocker (>40 пройдено), но < WATCH-порога
        decision = "SKIP"
        reason = (
            f"Confidence {conf:.1f} is below WATCH threshold {_WATCH_CONF_THRESHOLD}."
        )
    else:
        # conf >= 50 и нет blockers
        decision = _determine_take_or_watch(f, direction, conf, pos, warn)
        reason = _build_active_reason(decision, f, direction, conf)

    # ── итог ──────────────────────────────────────────────────────────────────
    summary_map = {
        "TAKE":  "Qualified setup; consider risk planning.",
        "WATCH": "Setup needs additional confirmation.",
        "SKIP":  "Setup does not meet current quality requirements.",
    }

    comp_scores = {
        "trend_quality":    round(f["tq_score"],  2) if f["tq_score"]  is not None else None,
        "volume_quality":   round(f["vq_score"],  2) if f["vq_score"]  is not None else None,
        "breakout_quality": round(f["bq_score"],  2) if f["bq_score"]  is not None else None,
        "structure_quality": round(f["sq_score"], 2) if f["sq_score"]  is not None else None,
    }

    market_ctx = {
        "structure":    f["ms_structure"],
        "alignment":    sq_alignment,
        "bos_direction": f["bos_dir"],
        "choch":         f["choch"],
    }

    return _build_result(
        direction=direction,
        decision=decision,
        decision_score=round(decision_score, 2),
        confidence=round(conf, 2) if conf is not None else None,
        conf_label=f["conf_label"],
        signal=sig,
        bq_confirmed=f["bq_confirmed"],
        avail_comps=f["avail_comps"],
        pos=pos,
        warn=warn,
        blockers=blockers,
        comp_scores=comp_scores,
        market_ctx=market_ctx,
        reason=reason,
    )


# ─── вспомогательные функции решения ─────────────────────────────────────────

def _determine_take_or_watch(
    f: dict,
    direction: str,
    conf: float,
    pos: list[str],
    warn: list[str],
) -> str:
    """
    Возвращает "TAKE" или "WATCH" при отсутствии hard blockers.
    TAKE требует выполнения всех строгих критериев.
    """
    if conf < _TAKE_CONF_THRESHOLD:
        return "WATCH"

    # conf >= 70 — проверяем мягкие критерии TAKE
    soft_fails: list[str] = []

    if not f["bq_confirmed"]:
        soft_fails.append("breakout not confirmed")
    if f["signal"] != direction:
        soft_fails.append(f"pipeline signal is {f['signal']}, not {direction}")
    if f["bq_score"] is not None and f["bq_score"] < _TAKE_BREAKOUT_MIN:
        soft_fails.append(f"breakout_score {f['bq_score']:.1f} < {_TAKE_BREAKOUT_MIN}")
    if f["tq_score"] is not None and f["tq_score"] < _TAKE_TREND_MIN:
        soft_fails.append(f"trend_quality_score {f['tq_score']:.1f} < {_TAKE_TREND_MIN}")
    if f["sq_alignment"] == "OPPOSED":
        soft_fails.append("structure alignment is OPPOSED")
    if f["avail_comps"] < _MIN_COMPONENTS_TAKE:
        soft_fails.append(f"only {f['avail_comps']} of 4 components available")

    # Мягкие предупреждения WATCH даже при conf>=70
    watch_triggers: list[str] = []
    if f["vq_score"] is not None and f["vq_score"] < _WATCH_VOLUME_WEAK:
        watch_triggers.append("weak volume")
    if f["sq_alignment"] == "TRANSITION":
        watch_triggers.append("structure in transition")
    if f["ms_structure"] == "RANGE":
        watch_triggers.append("market is ranging")

    if soft_fails or watch_triggers:
        return "WATCH"

    return "TAKE"


def _build_skip_reason(blockers: list[dict], f: dict) -> str:
    msgs = [b["message"] for b in blockers]
    return "SKIP: " + "; ".join(msgs)


def _build_active_reason(decision: str, f: dict, direction: str, conf: float) -> str:
    parts: list[str] = []
    if decision == "TAKE":
        parts.append(f"{direction} setup qualifies: "
                     f"confidence={conf:.1f}, "
                     f"breakout confirmed, "
                     f"all criteria met")
    else:
        # WATCH — объясняем что хорошо и что не хватает
        if conf >= _TAKE_CONF_THRESHOLD:
            parts.append(f"confidence={conf:.1f} is high, but soft criteria unmet")
        else:
            parts.append(f"confidence={conf:.1f} ({_WATCH_CONF_THRESHOLD}–{_TAKE_CONF_THRESHOLD-0.001:.0f} range)")
        if f["ms_structure"] == "RANGE":
            parts.append("market is ranging")
        if f["sq_alignment"] == "TRANSITION":
            parts.append("structure in transition")
        if f["vq_score"] is not None and f["vq_score"] < _WATCH_VOLUME_WEAK:
            parts.append(f"volume score weak ({f['vq_score']:.1f})")
        if f["signal"] == "WAIT":
            parts.append("pipeline signal is WAIT")
        if f["tq_score"] is not None and f["tq_score"] < _TAKE_TREND_MIN:
            parts.append(f"trend quality low ({f['tq_score']:.1f})")
        if f["bq_score"] is not None and f["bq_score"] < _TAKE_BREAKOUT_MIN:
            parts.append(f"breakout score low ({f['bq_score']:.1f})")
    return "WATCH: " + "; ".join(parts) if decision == "WATCH" else "; ".join(parts)


def _build_result(
    direction: str,
    decision: str,
    decision_score: float,
    confidence: float | None,
    conf_label: str,
    signal: str,
    bq_confirmed: bool,
    avail_comps: int,
    pos: list[str],
    warn: list[str],
    blockers: list[dict],
    comp_scores: dict,
    market_ctx: dict,
    reason: str,
) -> dict:
    summary_map = {
        "TAKE":  "Qualified setup; consider risk planning.",
        "WATCH": "Setup needs additional confirmation.",
        "SKIP":  "Setup does not meet current quality requirements.",
    }
    return {
        "direction":            direction,
        "decision":             decision,
        "decision_score":       round(float(decision_score), 2),
        "confidence":           confidence,
        "confidence_label":     conf_label,
        "signal":               signal,
        "breakout_confirmed":   bq_confirmed,
        "available_components": avail_comps,
        "positive_factors":     pos,
        "warning_factors":      warn,
        "blockers":             blockers,
        "component_scores":     comp_scores,
        "market_context":       market_ctx,
        "summary":              summary_map.get(decision, ""),
        "reason":               reason,
    }


# ─── evaluate_both_directions ─────────────────────────────────────────────────

def evaluate_both_directions(
    quality_analysis: dict | None,
) -> dict:
    """
    Запускает evaluate_trade_decision для LONG и SHORT,
    затем выбирает итоговое FINAL-решение.

    Параметры:
        quality_analysis — результат analyze_both_directions(df):
                           {"LONG": ..., "SHORT": ..., "FINAL": ...}
                           или None.

    Возвращает:
        {
            "LONG":  decision_long,
            "SHORT": decision_short,
            "FINAL": {
                "decision":       "TAKE" | "WATCH" | "SKIP",
                "direction":      "LONG" | "SHORT" | "NONE",
                "decision_score": float,
                "confidence":     float,
                "reason":         str,
            }
        }

    Приоритет решений: TAKE > WATCH > SKIP.
    """
    _priority = {"TAKE": 3, "WATCH": 2, "SKIP": 1}

    if not isinstance(quality_analysis, dict):
        _empty = _build_result(
            "LONG", "SKIP", 0.0, None, "UNKNOWN", "UNKNOWN",
            False, 0, [], [],
            [{"code": _BLOCKER_NO_DATA, "message": "quality_analysis is None or not a dict"}],
            {"trend_quality": None, "volume_quality": None,
             "breakout_quality": None, "structure_quality": None},
            {"structure": "UNKNOWN", "alignment": "UNKNOWN",
             "bos_direction": "NONE", "choch": "NONE"},
            "No quality analysis available.",
        )
        _empty_s = dict(_empty)
        _empty_s["direction"] = "SHORT"
        return {
            "LONG":  _empty,
            "SHORT": _empty_s,
            "FINAL": {
                "decision":       "SKIP",
                "direction":      "NONE",
                "decision_score": 0.0,
                "confidence":     0.0,
                "reason":         "No quality analysis available.",
            },
        }

    long_qr  = quality_analysis.get("LONG")  if isinstance(quality_analysis.get("LONG"),  dict) else None
    short_qr = quality_analysis.get("SHORT") if isinstance(quality_analysis.get("SHORT"), dict) else None

    long_decision  = evaluate_trade_decision(long_qr,  "LONG")
    short_decision = evaluate_trade_decision(short_qr, "SHORT")

    # ── FINAL-логика ──────────────────────────────────────────────────────────
    ld = long_decision["decision"]
    sd = short_decision["decision"]
    ls = long_decision["decision_score"]
    ss = short_decision["decision_score"]
    lc = long_decision["confidence"]  or 0.0
    sc = short_decision["confidence"] or 0.0

    lp = _priority[ld]
    sp = _priority[sd]

    if lp > sp:
        # LONG wins по типу
        final_dec = ld
        final_dir = "LONG"
        final_score = ls
        final_conf  = lc
        final_reason = f"LONG decision={ld} outranks SHORT decision={sd}"

    elif sp > lp:
        # SHORT wins по типу
        final_dec = sd
        final_dir = "SHORT"
        final_score = ss
        final_conf  = sc
        final_reason = f"SHORT decision={sd} outranks LONG decision={ld}"

    else:
        # Одинаковый тип — выбираем по decision_score
        if ls > ss:
            final_dec = ld
            final_dir = "LONG"
            final_score = ls
            final_conf  = lc
            final_reason = (
                f"Both {ld}; LONG score={ls:.1f} > SHORT score={ss:.1f}"
            )
        elif ss > ls:
            final_dec = sd
            final_dir = "SHORT"
            final_score = ss
            final_conf  = sc
            final_reason = (
                f"Both {sd}; SHORT score={ss:.1f} > LONG score={ls:.1f}"
            )
        else:
            # Полное равенство
            if ld == "SKIP":
                final_dec = "SKIP"
                final_dir = "NONE"
                final_score = 0.0
                final_conf  = 0.0
                final_reason = "Both directions SKIP"
            elif ld == "TAKE":
                final_dec = "WATCH"
                final_dir = "NONE"
                final_score = ls
                final_conf  = max(lc, sc)
                final_reason = "Conflicting equal-quality TAKE decisions"
            else:
                final_dec = "WATCH"
                final_dir = "NONE"
                final_score = ls
                final_conf  = max(lc, sc)
                final_reason = "Both WATCH with equal decision scores"

    return {
        "LONG":  long_decision,
        "SHORT": short_decision,
        "FINAL": {
            "decision":       final_dec,
            "direction":      final_dir,
            "decision_score": round(float(final_score), 2),
            "confidence":     round(float(final_conf), 2),
            "reason":         final_reason,
        },
    }
