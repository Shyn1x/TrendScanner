"""
confidence.py
~~~~~~~~~~~~~
Объединяет оценки независимых модулей качества и рассчитывает
итоговую уверенность сигнала (Confidence Score, 0–100).

Компоненты и базовые веса (при наличии всех трёх, сумма = 1.0):
    • trend_quality   — качество трендовой линии   (базовый вес 50%)
    • volume_quality  — подтверждение объёмом       (базовый вес 20%)
    • breakout_quality — сила и чёткость пробоя    (базовый вес 30%)

Планируемые компоненты (добавляются без изменения сигнатуры):
    • atr_quality       — качество пробоя в единицах ATR
    • market_structure  — структура рынка (HH/HL, LH/LL)
    • multi_tf_quality  — согласованность таймфреймов
    • ml_score          — оценка ML-модели

ПЕРЕНОРМИРОВКА ВЕСОВ:
    Если компонент отсутствует (None или {}), его базовый вес перераспределяется
    пропорционально между доступными компонентами.
    confidence не занижается при неполных данных.

    Примеры:
        все три         → w_tq=0.50, w_vq=0.20, w_bq=0.30
        нет volume      → w_tq=0.625, w_bq=0.375     (0.50/0.80, 0.30/0.80)
        только breakout → w_bq=1.0

НУЛЕВЫЕ ЗАВИСИМОСТИ: scanner.py, Streamlit, analysis.py, multi_tf.py не импортируются.
"""

from __future__ import annotations
import math


# ─── базовые веса компонентов ────────────────────────────────────────────────
# При полном наборе компонентов сумма активных весов = 1.0.
# При добавлении нового компонента — перераспределить веса здесь.

WEIGHTS: dict[str, float] = {
    "trend_quality":    0.50,
    "volume_quality":   0.20,
    "breakout_quality": 0.30,
    # "atr_quality":       0.00,
    # "market_structure":  0.00,
    # "multi_tf_quality":  0.00,
    # "ml_score":          0.00,
}

# Отображение компонентов на ключи их словарей
_SCORE_KEYS: dict[str, str] = {
    "trend_quality":    "trend_quality_score",
    "volume_quality":   "volume_score",
    "breakout_quality": "breakout_score",
}


# ─── метки уверенности ───────────────────────────────────────────────────────

def confidence_label(confidence: float) -> str:
    """
    Переводит числовую оценку Confidence в текстовую метку.

        "LOW"       — 0–39
        "MEDIUM"    — 40–69
        "HIGH"      — 70–84
        "VERY HIGH" — 85–100
    """
    if confidence >= 85:
        return "VERY HIGH"
    elif confidence >= 70:
        return "HIGH"
    elif confidence >= 40:
        return "MEDIUM"
    else:
        return "LOW"


# ─── безопасное извлечение оценки ────────────────────────────────────────────

def _extract_score(quality_dict: dict, key: str) -> float:
    """
    Безопасно извлекает числовую оценку из словаря качества.
    Возвращает 0.0 если ключ отсутствует или значение некорректно.
    """
    try:
        val = float(quality_dict.get(key, 0.0))
        return val if math.isfinite(val) else 0.0
    except (TypeError, ValueError):
        return 0.0


# ─── основная функция ────────────────────────────────────────────────────────

def calculate_confidence(
    trend_quality:    dict,
    volume_quality:   dict,
    breakout_quality: dict | None = None,
) -> dict:
    """
    Объединяет оценки всех модулей качества и рассчитывает
    итоговый Confidence Score с перенормировкой весов.

    Параметры:
        trend_quality    — dict от calc_trend_quality()
                           Ключ: "trend_quality_score"
        volume_quality   — dict от score_volume()
                           Ключ: "volume_score"
        breakout_quality — dict от calculate_breakout_quality() или None
                           Ключ: "breakout_score"

    Возвращает:
        {
            "confidence":          float — итоговая оценка 0–100,
            "label":               str   — "LOW" / "MEDIUM" / "HIGH" / "VERY HIGH",
            "components": {
                "trend_quality": {
                    "score":            float,
                    "base_weight":      float,  — базовый вес из WEIGHTS
                    "effective_weight": float,  — перенормированный вес
                    "contribution":     float,  — score * effective_weight
                    "available":        bool    — компонент присутствует
                },
                "volume_quality":   { ... },
                "breakout_quality": { ... }
            },
            "available_components": int — количество доступных компонентов,
            "reason":               str — описание результата
        }

    Добавление нового компонента:
        1. Добавить параметр: `atr_quality: dict | None = None`
        2. Добавить в _inputs ниже
        3. Добавить ключ в _SCORE_KEYS
        4. Обновить WEIGHTS (сумма = 1.0 при полном наборе)
    """

    # ── входные данные компонентов ────────────────────────────────────────────
    _inputs: dict[str, dict | None] = {
        "trend_quality":    trend_quality,
        "volume_quality":   volume_quality,
        "breakout_quality": breakout_quality,
    }

    # ── определяем доступность каждого компонента ─────────────────────────────
    # Компонент «доступен» если его dict непустой (не None и не {})
    # и его базовый вес > 0.
    available_names = [
        name
        for name in _inputs
        if _inputs[name] and WEIGHTS.get(name, 0.0) > 0.0
    ]
    n_available = len(available_names)

    # ── перенормировка весов ──────────────────────────────────────────────────
    total_base_w = sum(WEIGHTS.get(name, 0.0) for name in available_names)

    # ── строим components для всех трёх полей ─────────────────────────────────
    raw_sum = 0.0
    components: dict[str, dict] = {}

    for name in _inputs:
        base_w     = WEIGHTS.get(name, 0.0)
        is_avail   = name in available_names
        score_key  = _SCORE_KEYS.get(name, "")
        d          = _inputs[name] or {}

        if is_avail and total_base_w > 0.0:
            score   = _extract_score(d, score_key)
            eff_w   = base_w / total_base_w
            contrib = score * eff_w
            raw_sum += contrib
        else:
            score   = 0.0
            eff_w   = 0.0
            contrib = 0.0

        components[name] = {
            "score":            round(score, 1),
            "base_weight":      round(base_w, 4),
            "effective_weight": round(eff_w, 4),
            "contribution":     round(contrib, 1),
            "available":        is_avail,
        }

    # ── итоговый confidence ───────────────────────────────────────────────────
    if not math.isfinite(raw_sum):
        raw_sum = 0.0

    confidence_val = float(max(0.0, min(100.0, raw_sum)))

    # ── reason ────────────────────────────────────────────────────────────────
    total_components = len([k for k, v in WEIGHTS.items() if v > 0.0])
    if n_available == 0:
        reason = "Нет доступных компонентов — confidence не рассчитан"
    elif n_available == total_components:
        joined = ", ".join(available_names)
        reason = f"Все {total_components} компонента доступны ({joined})"
    else:
        joined    = ", ".join(available_names)
        missing   = [n for n in _inputs if n not in available_names
                     and WEIGHTS.get(n, 0.0) > 0.0]
        m_joined  = ", ".join(missing)
        reason = (
            f"{n_available}/{total_components} компонентов доступны: {joined}; "
            f"отсутствуют: {m_joined}; перенормировка весов применена"
        )

    return {
        "confidence":          round(confidence_val, 2),
        "label":               confidence_label(confidence_val),
        "components":          components,
        "available_components": n_available,
        "reason":              reason,
    }
