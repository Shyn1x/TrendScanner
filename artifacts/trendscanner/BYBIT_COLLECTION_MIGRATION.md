# Prospective collection on Bybit

Live scheduled 15m, 1h and 4h collection now uses Bybit mainnet USDT linear
perpetual trade candles, with the existing 25-symbol universe and frozen READY
calculations. Public candle requests do not use trading credentials. Collection
and outcome jobs run on the Linux `bybit-demo` runner.

The workflow explicitly sets `TREND_SCANNER_MARKET_SOURCE=bybit_linear`.
The adapter defaults to `kucoin_futures` for archived research workflows, so
historical KuCoin replays and their experiment identity remain reproducible.
There is no fallback between venues, spot and futures, or contract aliases.

## Separate experiment identities

| Series | Archived KuCoin | New Bybit |
| --- | --- | --- |
| 15m / 1h | `lower-tf-ready-v5-fixed200-catchup` | `lower-tf-ready-v6-bybit-fixed200-catchup` |
| 4h | `market-regime-v2-sequential` | `market-regime-v3-bybit-sequential` |

Each new series begins at its first successful current closed candle and then
continues sequentially. The lower-TF first observation establishes baseline
state; it does not manufacture a False-to-True event from a first READY=True.
Old coverage, events and outcomes are retained. Missing KuCoin history is not
replaced with Bybit candles or presented as recovered KuCoin observations.

The Bybit adapter validates instrument type, closed boundaries, finite OHLCV,
exact consecutive timestamps and uniqueness. It paginates backwards for the
1213-row 4h market context. All 4h context symbols use the same Bybit loader as
the signal candidates. API failures, missing candles or unavailable instruments
fail before prospective writes rather than generating flat replacement candles.

## Demo execution and outcome interpretation

New demo entries accept only fresh 1h LONG transitions from the Bybit lower-TF
experiment. Position sizing, limits, 1.5% Mark Price stop and twelve-bar exit
remain unchanged. The executor policy version and existing trade journal stay
in place so existing positions can still be reconciled and closed. Trade updates
include the original experiment identity; new order IDs also include that
identity. Legacy reservations without an exchange order are skipped rather than
opening a new trade from an old KuCoin signal. The virtual portfolio equity and
aggregate risk limits continue across the source migration.

The scheduled outcome analyzer reads only the new Bybit experiment and obtains
its prices from Bybit. Its fixed-stop research metrics still use trade-candle
highs/lows with ideal stop fills, excluding fees, funding and slippage. These
metrics are not an exact simulation of the demo's Mark Price trigger or actual
entry delay. Actual demo PnL must be evaluated from the separate trade journal.

The Streamlit scanner and old historical research source are unchanged by this
prospective migration. Manual collection defaults to dry-run; select `write`
to establish or advance the new series. The demo workflow remains demo-only.
