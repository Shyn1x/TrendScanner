# Lower-timeframe prospective outcomes

This is a derived research layer for the immutable 1h/15m prospective READY events.
It does not change READY rules, signal collection, production decisions, or the 4h prospective population.

## What is measured

For every stored post-baseline READY False->True event, outcomes are evaluated after 1, 3, 6, and 12 fully closed candles of the event timeframe.

Each completed horizon stores:

- entry reference: close of the READY signal candle
- MFE: maximum favorable move after the signal
- MAE: maximum adverse move after the signal
- close return: directional return at the end of the horizon
- bars to MFE / MAE
- count of bounded no-tick slots repaired with the same flat previous-close / zero-volume policy used by the lower-TF collector
- the original stored `target_4h_timestamp` anchor

The same completed windows are also evaluated against a preregistered fixed
Stop Loss grid:

- `0.50%`
- `0.75%`
- `1.00%`
- `1.50%`

For every stop and horizon, the analyzer stores the stop price, whether the
stop was touched, the first bar that touched it, the original horizon close
return, and the stop-adjusted trade return. A touch counts as a hit. If no stop
is hit, the stop-adjusted return is the directional close return at the end of
the horizon; if a stop is hit, it is the negative fixed stop percentage.

This is an OHLC research model. It assumes execution at the stop price and does
not include fees, funding, or slippage. Because no Take Profit is modeled here,
there is no ambiguous TP-versus-SL ordering inside one candle.

A horizon is never written before its final candle is fully closed. Missing data beyond the bounded no-tick policy fails closed.

## Storage

Derived rows use the separate table `lower_tf_prospective_outcomes` and version:

`lower-tf-outcome-v1-bars-1-3-6-12`

Stop-aware rows use the separate table
`lower_tf_prospective_stop_outcomes` and version:

`lower-tf-stop-v1-fixed-bps-50-75-100-150`

The source event rows remain untouched. Both derived datasets use idempotent,
versioned primary keys. Existing outcome rows are not rewritten; the stop layer
can backfill every already-mature event and then continue incrementally.

## Scheduling

`.github/workflows/lower-tf-outcomes.yml` runs once per day. Manual runs default to dry-run; writing can be explicitly enabled with the workflow input.

The analyzer uses the frozen lower-TF research base `5547e881078dc97ccb317b5e4f8252eab3fb9a21` plus the current outcome-analyzer patch, matching the same frozen strategy used by the collector.

## Future P3 analysis

The analyzer deliberately does not classify market regime during outcome collection. Each prospective event already contains the exact 4h context anchor that was known when the signal closed. This lets later analysis apply the preregistered frozen 4h market-regime logic to compare, without hindsight:

`LONG + market_regime=MIXED + volatility=NORMAL`

against all other lower-timeframe READY events.
