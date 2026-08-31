import numpy as np


def find_pivots(df, window=5):

    highs = []
    lows = []

    if len(df) <= 2 * window:
        return highs, lows

    high_values = df.high.to_numpy()
    low_values = df.low.to_numpy()

    for i in range(window, len(df)-window):

        high = high_values[i]
        low = low_values[i]

        if high == max(
            high_values[i-window:i+window]
        ):
            highs.append(
                (i, high)
            )

        if low == min(
            low_values[i-window:i+window]
        ):
            lows.append(
                (i, low)
            )

    return highs, lows


def create_trendline(points):

    if len(points) < 2:
        return None

    p1 = points[-2]
    p2 = points[-1]

    x1, y1 = p1
    x2, y2 = p2

    slope = (
        y2 - y1
    ) / (x2 - x1)

    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "slope": slope
    }


def line_value(line, x):

    return (
        line["y1"]
        +
        line["slope"]
        *
        (x - line["x1"])
    )


def check_break(df, line, direction):

    if line is None:
        return "WAIT"

    last = len(df) - 1

    price = df.close.iloc[-1]
    prev_price = df.close.iloc[-2]

    current_line = line_value(
        line,
        last
    )

    previous_line = line_value(
        line,
        last - 1
    )

    if direction == "LONG":

        if (
            prev_price < previous_line
            and
            price > current_line
        ):
            return "LONG"

    if direction == "SHORT":

        if (
            prev_price > previous_line
            and
            price < current_line
        ):
            return "SHORT"

    return "WAIT"
