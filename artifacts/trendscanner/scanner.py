import ccxt
import pandas as pd

from trendlines import find_pivots, create_trendline, check_break

exchange = ccxt.kucoinfutures()


def _to_futures_symbol(symbol: str) -> str:
    """
    Конвертирует символ из формата Spot (BTC/USDT) в формат KuCoin Futures
    (BTC/USDT:USDT — линейный бессрочный контракт).
    Если символ уже в правильном формате — возвращает без изменений.
    """
    if ":" not in symbol:
        base, quote = symbol.split("/")
        return f"{base}/{quote}:{quote}"
    return symbol


def get_futures_symbols():

    markets = exchange.load_markets()

    symbols = []

    for symbol, data in markets.items():

        if (
            symbol.endswith(":USDT")       # формат KuCoin Futures: BTC/USDT:USDT
            and data.get("active")
        ):
            symbols.append(symbol)

    return symbols


def _build_monthly_from_daily(futures_symbol: str) -> pd.DataFrame:
    """
    KuCoin Futures не поддерживает 1M (месячные) свечи через API.
    Строим 1M ресемплингом из дневных данных.
    500 дней ≈ 16 закрытых месяцев — достаточно для трендового анализа.
    Текущий незакрытый месяц исключается.
    """
    candles = exchange.fetch_ohlcv(futures_symbol, "1d", limit=500)
    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["dt"] = pd.to_datetime(df["time"], unit="ms")
    df = df.set_index("dt")

    monthly = df.resample("MS").agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna()

    # Убираем последний (текущий, незакрытый) месяц
    if len(monthly) > 1:
        monthly = monthly.iloc[:-1]

    monthly = monthly.reset_index()
    # ms-timestamp обратно
    monthly["time"] = (monthly["dt"].astype("int64") // 10 ** 6)
    monthly = monthly.drop(columns=["dt"])

    return monthly[["time", "open", "high", "low", "close", "volume"]]


def get_data(symbol: str, timeframe: str) -> pd.DataFrame:
    futures_symbol = _to_futures_symbol(symbol)

    # KuCoin Futures API не поддерживает 1M OHLCV напрямую —
    # строим из дневных данных.
    if timeframe == "1M":
        return _build_monthly_from_daily(futures_symbol)

    candles = exchange.fetch_ohlcv(futures_symbol, timeframe, limit=300)
    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return df


def trend_signal(df: pd.DataFrame) -> str:
    highs, lows = find_pivots(df)

    down_line = create_trendline(highs)
    up_line   = create_trendline(lows)

    long_signal  = check_break(df, down_line, "LONG")
    short_signal = check_break(df, up_line,   "SHORT")

    if long_signal == "LONG":
        return "LONG"
    if short_signal == "SHORT":
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
