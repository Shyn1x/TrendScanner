import ccxt
import pandas as pd

exchange = ccxt.bybit({"options": {"defaultType": "spot"}})


def get_data(symbol: str, timeframe: str) -> pd.DataFrame:
    candles = exchange.fetch_ohlcv(symbol, timeframe, limit=300)
    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return df


def trend_signal(df: pd.DataFrame) -> str:
    last = df.close.iloc[-1]
    previous_high = df.high.iloc[-20:-1].max()
    previous_low  = df.low.iloc[-20:-1].min()

    if last > previous_high:
        return "LONG"
    if last < previous_low:
        return "SHORT"
    return "WAIT"


def analyze(symbol: str) -> dict:
    from config import TIMEFRAMES
    result = {}
    for tf in TIMEFRAMES:
        try:
            df = get_data(symbol, tf)
            result[tf] = trend_signal(df)
        except Exception:
            result[tf] = "ERROR"
    return result
