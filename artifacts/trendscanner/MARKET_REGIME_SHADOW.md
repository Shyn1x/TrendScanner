# Market-regime prospective shadow

Status: standalone module, **not connected to the live app**. No historical
replay, HOLDOUT rerun, threshold adjustment, or production change is included.
The HOLDOUT2 results in the handoff are user-supplied research context; this
implementation does not independently reproduce them or infer profitability.

## Architecture audit and live integration blocker

- `analysis.analyze_timeframe` finishes decisions and returns quality/decision
  fields. `multi_tf.multi_analysis` assembles timeframe results and FINAL.
- `app.py` finishes `all_results` before calling
  `persist_completed_scan_analytics`. That is an existing post-decision point.
- Later, `ready_ui.render_ready_section` calls
  `ready_engine.build_ready_report(all_results)`. Its candidates carry `ready`,
  symbol, timeframe, direction, production_decision, confidence and decision_score.
- **READY candidates have no source candle timestamp.** `multi_tf` does not retain
  the dataframe timestamp in the timeframe result; `analysis` does not export it.
  The analytics trigger timestamp reader only checks optional fields and cannot
  establish the READY observation candle. A breakout's timestamp is not necessarily
  the READY candle. Streamlit refresh time and the shadow context candle are not
  valid substitutes either.
- Therefore the module neither calls a READY selector nor adds a live hook/UI.
  `observe_ready` accepts an existing READY candidate only when its trustworthy
  original `ready_timestamp` is supplied; absence fails open without logging.
- Future integration requires carrying the original analysis candle timestamp
  alongside its cached result and exposing the already-created READY report to a
  post-decision observer. Preserve original decision fields and candidate ordering.
  No such architecture change is made in this patch.

The existing `context_trigger_shadow.py` experiment is untouched and not reused.

## Context and mapping

`exchange.milliseconds()` determines `boundary = floor(now / TIMEFRAME_MS) *
TIMEFRAME_MS`, and `target = boundary - TIMEFRAME_MS`. The research loader requests
214 candles with `get_research_data_before(symbol, "4h",
before_timestamp=boundary, total_limit=CONTEXT_BARS + 1)` for the same 25-symbol
universe as the READY research pilot. Every accepted window must be contiguous,
unique, exactly aligned to the target and contain finite, coherent positive OHLC.
No open or missing candle is substituted.

Reuse is direct: `ready_market_regime_analysis._frame_features` computes each
symbol's features; `_attach_market` computes BTC context/breadth and invokes the
existing `classify_regime`. The returned market volatility and ATR fields refer
to BTC, as in validated research, not the candidate asset. The target feature
must have exactly 200 previous ATR observations. No formulas are copied.

| Direction | Market | Volatility | Tag | Tier |
|---|---|---|---|---|
| LONG | MIXED | NORMAL | P3_MATCH | PRIMARY |
| LONG | BULL | NORMAL | P2_MATCH | EXPERIMENTAL |
| Other combinations | Any | Any | NONE | NONE |

P1 and P4 are not tags or filters. These labels never change trading decisions.

The module-level `_PROCESS_CONTEXT` cache is shared within one Python process.
A lock serializes the first build for each target. Returned snapshots are deep
copies. Both successful and unavailable snapshots are cached, so retries/cards
cannot cause additional loads at the same target. The next target triggers a new
snapshot; older targets remain cached. Tests use isolated injected instances.

Any missing symbol (including BTC), invalid window, network or internal error
produces UNAVAILABLE/NONE. Successfully loaded symbol count is retained; partial
breadth is not promoted to a candidate tag. This deliberately uses the permitted
UNAVAILABLE behavior rather than a partial-universe classification. Errors expose
short codes/types, not arbitrary exception text containing URLs or secrets.

## Observation and persistence contract

`observe_ready` returns a separate result and never mutates the candidate. It
requires `ready is True`, identity, original `ready_timestamp` (Unix ms), and the
existing production_decision, decision_score and confidence. Tests inject the
context clock, loader, observation time and temporary database; no network needed.

Each event contains:

- schema_version=1, shadow_version=`market-regime-v1-preregistered`, observed_at UTC;
- symbol, timeframe, direction, ready_timestamp;
- production: decision, decision_score, confidence;
- market: target_4h_timestamp, market_regime, volatility, btc_return_60,
  ema_distance, breadth_above_ema50, median_return_20, atr_pct,
  atr_percentile_past_200, available_symbols, prior_atr_observations;
- shadow: shadow_status, shadow_error, shadow_tag, candidate_tier.

No outcome is written. Missing market fields are null, never fabricated.

Existing analytics uses local SQLite but `analysis_records` is keyed by scan,
not READY candle, and has a fixed schema. Reuse its `DEFAULT_DB_PATH` and SQLite
storage with a separate `market_regime_shadow_events` table. No changes to the
existing analytics table/service are needed. Unique key:
`(shadow_version, symbol, timeframe, direction, ready_timestamp)`.

Transactions handle concurrent writes; INSERT OR IGNORE retains the first
observation, including UNAVAILABLE, across refreshes/process restarts. Duplicate
calls return that stored snapshot. UPDATE/DELETE triggers reject mutation via
normal SQL. Storage errors return UNAVAILABLE/NONE and do not propagate. The
one-second SQLite busy timeout bounds lock waiting. No database is created just
by importing the module, and no real observations are collected in this patch.

SQLite files are already ignored by the repository's `*.db`, `*.db-wal` and
`*.db-shm` patterns. No JSONL fallback or external database is introduced.
**Local Streamlit Cloud disk is not treated as durable storage**: restart or
redeployment may lose observations and their deduplication history. A future
collection deployment needs an explicit retention/export arrangement before
these observations can be relied upon as a complete validation dataset.

## Verification

Run with project dependencies installed:

```bash
PYTHONPATH=artifacts/trendscanner python3 -m py_compile \
  artifacts/trendscanner/market_regime_shadow.py \
  artifacts/trendscanner/test_market_regime_shadow.py
PYTHONPATH=artifacts/trendscanner python3 -m pytest -q \
  artifacts/trendscanner/test_market_regime_shadow.py
```

Tests cover mapping, exact closed windows, shared function calls, 200 prior ATR,
concurrent caching/rollover, missing/malformed data, fail-open errors, input
immutability, immutable event schema, concurrent deduplication and restart reuse.
UI and actual live collection remain intentionally unimplemented due to the
missing trustworthy READY timestamp hook described above.
