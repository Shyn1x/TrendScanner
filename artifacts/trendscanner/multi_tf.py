from scanner import get_data
from analysis import analyze_timeframe


TIMEFRAMES = [
    "1M",
    "1w",
    "1d",
    "4h",
    "1h"
]

WEIGHTS = {
    "1M": 40,
    "1w": 30,
    "1d": 20,
    "4h": 10,
    "1h": 5
}

MAX_SCORE = sum(WEIGHTS.values())  # 105


def multi_analysis(symbol):

    result = {}

    total_score = 0

    long_count = 0
    short_count = 0


    for tf in TIMEFRAMES:

        try:

            df = get_data(
                symbol,
                tf
            )

            analysis = analyze_timeframe(
                df
            )

            result[tf] = analysis

            weight = WEIGHTS.get(tf, 0)

            # считаем взвешенный баланс

            if analysis["signal"] == "LONG":

                total_score += weight
                long_count += 1

            elif analysis["signal"] == "SHORT":

                total_score -= weight
                short_count += 1

        except Exception as e:

            result[tf] = {
                "trend": "ERROR",
                "signal": str(e),
                "score": 0
            }


    # Финальное решение (порог 40 из 105)

    if total_score >= 40:

        final_signal = "LONG"

    elif total_score <= -40:

        final_signal = "SHORT"

    else:

        final_signal = "WAIT"


    result["FINAL"] = {

        "signal": final_signal,

        "score": abs(total_score),

        "max_score": MAX_SCORE,

        "confidence": round(abs(total_score) / MAX_SCORE * 100),

        "long_timeframes": long_count,

        "short_timeframes": short_count

    }

    return result
