# RSI2 1D + 4H breakout — shared-account portfolio (research only)

Run: https://github.com/Shyn1x/TrendScanner/actions/runs/44146842297

Same frozen 100 Bybit symbols, 2026-11-17 boundary, single $300 wallet.
Limits: three owned positions, 1% fee-inclusive stop risk per entry,
3% aggregate stop risk, 1.5x gross notional/equity cap. RSI2 first.

| Test | Early return | Early DD | Early trades | Later return | Later DD | Later trades |
|---|---:|---:|---:|---:|---:|---:|
| rsi2_only | +5.834% | 10.412% | 59 | +11.753% | 4.943% | 53 |
| breakout_only | +14.414% | 14.877% | 305 | +18.345% | 11.755% | 302 |
| combined | +3.635% | 17.921% | 253 | +6.033% | 14.217% | 229 |

## Combined strategy attribution
- train: COMPANION_4H: 209 trades, trade P&L $+1.977, RSI2_1D: 44 trades, trade P&L $+13.300
  Entry selections: {'accepted_breakout': 209, 'accepted_rsi': 44, 'blocked_capacity': 1770, 'blocked_owned': 0, 'blocked_risk': 86, 'breakout_signals': 2110, 'rsi_signals': 178}
- later: COMPANION_4H: 190 trades, trade P&L $+4.882, RSI2_1D: 39 trades, trade P&L $+16.055
  Entry selections: {'accepted_breakout': 190, 'accepted_rsi': 39, 'blocked_capacity': 1539, 'blocked_owned': 0, 'blocked_risk': 114, 'breakout_signals': 1870, 'rsi_signals': 248}

*Both windows are reused exploratory historical data. Survivorship bias, simplified fills, missing actual historical funding, cross-margin and mark-price liquidation. No live orders.*
