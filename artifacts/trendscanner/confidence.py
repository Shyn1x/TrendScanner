"""
confidence.py
~~~~~~~~~~~~~
Объединяет оценки независимых модулей качества и рассчитывает
итоговую уверенность сигнала (Confidence Score, 0–100).

Компоненты и базовые веса (при наличии всех четырёх, сумма = 1.0):
    • trend_quality     — качество трендовой линии       (базовый вес 40%)
    • volume_quality    — подтверждение объёмом           (базовый вес 15%)
    • breakout_quality  — сила и чёткость пробоя          (базовый вес 30%)
    • structure_quality — рыночная структура HH/HL, LH/LL (базовый вес 15%)

Планируемые компоненты (добавляются без изменения сигнатуры):
    • atr_quality       — качество пробоя в единицах ATR
    • multi_tf_quality  — согласованность таймфреймов
    • ml_score          — оценка ML-модели

ПЕРЕНОРМИРОВКА ВЕСОВ:
    Если компонент недоступен, его базовый вес перераспределяется
    пропорционально между доступными компонентами.
    Confidence не занижается при неполных данных.

    Примеры (три первых компонента без structure):
        все четыре         → w_tq=0.40, w_vq=0.15, w_bq=0.30, w_sq=0.15
        нет structure      → w_tq=0.471, w_vq=0.176, w_bq=0.353
        нет volume         → w_tq=0.471, w_bq=0.353, w_sq=0.176
        только breakout    → w_bq=1.0

КРИТЕРИИ ДОСТУПНОСТИ КОМПОНЕНТА:
    Компонент считается доступным только если:
    1. передан непустой dict (не None, не {}, не другой тип);
    2. в нём присутствует ожидаемый ключ score;
    3. значение score приводится к float;
    4. значение является конечным числом (не NaN, не inf);
    5. score приводится к диапазону 0–100 (clamp, не отклонение);
    6. для structure_quality: дополнительно available == True.

НУЛЕВЫЕ ЗАВИСИМОСТИ: scanner.py, Streamlit, analysis.py, multi_tf.py не импортируются.
"""

from __future__ import annotations
import math


# ─── базовые веса компонентов ────────────────────────────────────────────────
# При полном наборе компонентов сумма активных весов = 1.0.
# При добавлении нового компонента — перераспределить веса здесь.

WEIGHTS: dict[str, float] = {
    "trend_quality":     0.40,
    "volume_quality":    0.15,
    "breakout_quality":  0.30,
    "structure_quality": 0.15,
    # "atr_quality":      0.00,
    # "multi_tf_quality": 0.00,
    # "ml_score":         0.00,
}

# Отображение компонентов на ключи их словарей
_SCORE_KEYS: dict[str, str] = {
    "trend_quality":     "trend_quality_score",
    "volume_quality":    "volume_score",
    "breakout_quality":  "breakout_score",
    "structure_quality": "structure_score",
}


# ─── метки уверенности ───────────────────────────────────────────────────────

def confidence_label(confidence: float) -> str:
    """
    Переводит числовую оценку Confidence в текстовую метку.

        "LOW"       — 0–39
        "MEDIUM"    — 40–69
        "HIGH"      — 70–84
        "VERY HIGH" — 85–100

    Каждое значение от 0 до 100 однозначно попадает ровно в одну категорию.
    """
    if confidence >= 85:
        return "VERY HIGH"
    elif confidence >= 70:
        return "HIGH"
    elif confidence >= 40:
        return "MEDIUM"
    else:
        return "LOW"


# ─── парсинг одного компонента ────────────────────────────────────────────────

def _parse_component(
    quality_input,
    score_key: str,
    comp_name: str,
) -> tuple[float | None, bool, str]:
    """
    Разбирает входной словарь компонента и возвращает (score, is_available, reason).

    Возвращает is_available=True только при выполнении всех условий:
        1. quality_input — непустой dict
        2. score_key присутствует в словаре
        3. значение приводится к конечному float
        4. значение приводится к диапазону 0–100 (clamp)

    Для structure_quality дополнительно проверяется ключ available:
        Если available == False — компонент недоступен даже при score == 0.

    При is_available=True score — float в [0, 100].
    При is_available=False score — None.
    """
    if quality_input is None:
        return None, False, f"{comp_name}: не передан (None)"

    if not isinstance(quality_input, dict):
        return None, False, (
            f"{comp_name}: ожидается dict, "
            f"получен {type(quality_input).__name__}"
        )

    if not quality_input:
        return None, False, f"{comp_name}: передан пустой dict"

    # Для structure_quality: проверяем ключ available до чтения score
    if comp_name == "structure_quality":
        available_flag = quality_input.get("available", True)
        if available_flag is False:
            sq_reason = quality_input.get("reason", "недоступен")
            return None, False, f"{comp_name}: available=False ({sq_reason})"

    if score_key not in quality_input:
        return None, False, (
            f"{comp_name}: ключ '{score_key}' отсутствует "
            f"(ключи: {list(quality_input.keys())})"
        )

    raw = quality_input[score_key]
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None, False, (
            f"{comp_name}: не удалось привести к float: {raw!r}"
        )

    if not math.isfinite(val):
        return None, False, f"{comp_name}: значение не конечно ({val})"

    # Приводим к диапазону 0–100 (clamp, не отклонение)
    clamped = max(0.0, min(100.0, val))

    if clamped != val:
        reason = f"{comp_name}: score={val:.4g} приведён к {clamped:.4g}"
    else:
        reason = f"{comp_name}: score={clamped:.4g}, доступен"

    return clamped, True, reason


# ─── основная функция ────────────────────────────────────────────────────────

def calculate_confidence(
    trend_quality:     dict | None = None,
    volume_quality:    dict | None = None,
    breakout_quality:  dict | None = None,
    structure_quality: dict | None = None,
) -> dict:
    """
    Объединяет оценки всех модулей качества и рассчитывает
    итоговый Confidence Score с перенормировкой весов.

    Параметры:
        trend_quality     — dict от calc_trend_quality()
                            Ключ: "trend_quality_score"
        volume_quality    — dict от score_volume()
                            Ключ: "volume_score"
        breakout_quality  — dict от calculate_breakout_quality() или None
                            Ключ: "breakout_score"
        structure_quality — dict от score_structure_for_direction() или None
                            Ключ: "structure_score"
                            Дополнительно: "available" (bool)

    Возвращает:
        {
            "confidence":          float  — итоговая оценка 0–100,
            "label":               str    — "LOW" / "MEDIUM" / "HIGH" / "VERY HIGH",
            "components": {
                "trend_quality":     { score, base_weight, effective_weight,
                                       contribution, available, reason },
                "volume_quality":    { ... },
                "breakout_quality":  { ... },
                "structure_quality": { ... },
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
    _inputs: dict[str, object] = {
        "trend_quality":     trend_quality,
        "volume_quality":    volume_quality,
        "breakout_quality":  breakout_quality,
        "structure_quality": structure_quality,
    }

    # ── парсинг каждого компонента ────────────────────────────────────────────
    parsed: dict[str, tuple] = {}
    for name in _inputs:
        score_key = _SCORE_KEYS[name]
        parsed[name] = _parse_component(_inputs[name], score_key, name)

    # ── доступные компоненты и перенормировка весов ───────────────────────────
    available_names = [
        name for name, (_, is_avail, _) in parsed.items()
        if is_avail and WEIGHTS.get(name, 0.0) > 0.0
    ]
    n_available = len(available_names)
    total_base_w = sum(WEIGHTS.get(name, 0.0) for name in available_names)

    # ── сборка components и расчёт confidence ─────────────────────────────────
    raw_sum = 0.0
    components: dict[str, dict] = {}

    for name in _inputs:
        base_w               = WEIGHTS.get(name, 0.0)
        score, is_avail, reason = parsed[name]

        if is_avail and total_base_w > 0.0:
            eff_w   = base_w / total_base_w
            contrib = score * eff_w
            raw_sum += contrib
            score_out = round(score, 1)
        else:
            eff_w     = 0.0
            contrib   = 0.0
            score_out = None          # недоступный компонент — score не определён

        components[name] = {
            "score":            score_out,
            "base_weight":      round(base_w, 4),
            "effective_weight": round(eff_w, 4),
            "contribution":     round(contrib, 1),
            "available":        is_avail,
            "reason":           reason,
        }

    # ── итоговый confidence ───────────────────────────────────────────────────
    if not math.isfinite(raw_sum):
        raw_sum = 0.0

    confidence_val = float(max(0.0, min(100.0, raw_sum)))

    # ── reason (итоговый) ─────────────────────────────────────────────────────
    total_components = len([k for k, v in WEIGHTS.items() if v > 0.0])
    if n_available == 0:
        summary = "Нет доступных компонентов — confidence не рассчитан"
    elif n_available == total_components:
        joined  = ", ".join(available_names)
        summary = f"Все {total_components} компонента доступны ({joined})"
    else:
        joined   = ", ".join(available_names)
        missing  = [
            n for n in _inputs
            if n not in available_names and WEIGHTS.get(n, 0.0) > 0.0
        ]
        m_joined = ", ".join(missing)
        summary  = (
            f"{n_available}/{total_components} компонентов доступны: {joined}; "
            f"отсутствуют: {m_joined}; перенормировка весов применена"
        )

    return {
        "confidence":           round(confidence_val, 2),
        "label":                confidence_label(confidence_val),
        "components":           components,
        "available_components": n_available,
        "reason":               summary,
    }
