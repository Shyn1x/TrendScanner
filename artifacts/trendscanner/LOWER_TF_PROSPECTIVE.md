# Lower-timeframe prospective experiment

This is a separate research experiment for 1h and 15m. It must not alter the validated 4h prospective P3 population, tables, workflow, or production decisions.

## Frozen population

- Symbols: the same 25-symbol research universe from `ready_outcome_pilot.py`.
- Timeframes: `1h` and `15m` only.
- Directions: LONG and SHORT.
- READY logic: the existing READY thresholds and decision inputs are reused unchanged. The 15m research adapter uses the same rules as 1h; only the timeframe whitelist is bypassed inside the research collector.
- A missing/invalid analysis is never converted to READY=False.

## Event contract

The experiment stores durable state for every symbol/timeframe/direction and immutable event rows only for post-baseline False->True READY transitions.

The first valid observation for an identity is baseline only. If it is READY=True, it seeds state as True but does not count as a signal event. This prevents an unknown pre-experiment state from being misclassified as a new transition.

Repeated or stale candle timestamps never create new events or roll state backward.

## P3 extension rule

The lower-timeframe P3 hypothesis is preregistered as:

`READY False->True AND direction=LONG AND 4h market_regime=MIXED AND 4h volatility=NORMAL`

The market context is the latest fully closed 4h candle that was available when the lower-timeframe signal candle closed.

For a lower-timeframe READY candle with open timestamp `t` and duration `d`:

`signal_close = t + d`

`target_4h = floor(signal_close / 4h) * 4h - 4h`

That exact `target_4h_timestamp` is stored with every transition. Classification is intentionally not fetched during collection; this keeps the collector light and avoids losing an immutable event because a large 4h context fetch failed at signal time.

Future P3 classification must use the frozen market-regime v1 logic and the validated 1213-row 4h context requirement. The stored anchor timestamp must not be replaced by current time or a later candle.

## Storage isolation

Lower-timeframe data uses separate Neon tables:

- `lower_tf_prospective_state`
- `lower_tf_prospective_events`

The 4h tables remain untouched.

## Collection safety

Before any writes, each batch must contain exactly 50 valid evaluations (25 symbols x 2 directions), all aligned to one expected latest-closed candle timestamp. Any unavailable analysis, stale batch, database failure, or unknown storage status must fail the job visibly.
