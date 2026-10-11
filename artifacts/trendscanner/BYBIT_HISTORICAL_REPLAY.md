# Separate Bybit historical portfolio replay

Run **Bybit Historical 1h LONG Portfolio Replay** on main with 1600 bars.
The frozen strategy base is 5547e881078dc97ccb317b5e4f8252eab3fb9a21,
overlaid with the reviewed Bybit source/contract modules and replay code.
Archived KuCoin workflow names and default source/version remain available.

The existing Linux VPS downloads public linear USDT perpetual trade candles
for 25 symbols with one shared closed hourly boundary. Native exact slots are
required. A JSON snapshot with per-symbol SHA256 hashes is uploaded as a run
artifact. Five Ubuntu calculation jobs consume that snapshot offline; none
fetches candles from another exchange. Each report carries the same snapshot
hash, and portfolio aggregation rejects mixed venues or different snapshots.
No database or exchange credentials are passed to this workflow. It cannot
write prospective observations or submit orders. The VPS runtime is independent.

The decision window, temporal fixes and False-to-True READY contract are reused.
The chronological development / holdout A / holdout B partitions reset their
baseline independently and keep all 12 future outcome candles inside a split.

Predeclared portfolio assumptions: 1h LONG, 300 USDT starting balance, 1% nominal
stop risk per trade, 3% aggregate nominal risk, at most three simultaneous
positions, at most 3x aggregate notional, 12-bar exit. **150bp stop is the primary
demo comparison**; 100bp is an existing predefined research comparison, not a
parameter chosen after this run. Commission is assumed 6bp and slippage 2bp per
side. Costs can make a stopped trade lose more than its nominal 1% risk.

This is not an exact reconstruction of Demo fills: entry uses the signal close,
historical stops use trade-candle extremes, whereas Demo uses MarkPrice stops.
Funding, exchange lot/minimum-order rounding, latency, partial fills and
intratrade mark-to-market drawdown are not modeled. Reported drawdown is the
realised-balance drawdown. Preserve these limitations when interpreting results.
