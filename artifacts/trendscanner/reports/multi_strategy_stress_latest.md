# Multi-strategy stress matrix — 84 scenarios, research only

Run: https://github.com/Shyn1x/TrendScanner/actions/runs/44165509369

**Seven prior candidates × four disjoint/full coin subsets × 16/32/48 bps all-in cost per side.**
All-in cost = estimated commission + assumed proportional slippage, not real fills.
Data end 2026-11-17. Both temporal windows already researched, NOT untouched OOS.

## Full 100-symbol universe — profitability versus cost
| Candidate | Early 16bp | Later 16bp | Later 32bp | Later 48bp | Later DD 48bp | Later trades 48bp | Subset positive pairs / 9 |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout30_slots1_risk0.5_tp3R | +11.76% | +13.68% | +11.44% | +8.17% | 7.91% | 137 | 0/9 |
| breakout60_slots1_risk1_tp3R | +17.13% | +11.16% | +15.25% | +0.34% | 13.24% | 134 | 1/9 |
| range_breakout_slots1_risk0.5_tp2R | +12.28% | +8.52% | +7.02% | +5.08% | 5.62% | 123 | 0/9 |
| pullback_reclaim_slots1_risk1_tp2R | +8.87% | +16.03% | +14.73% | +7.18% | 10.03% | 130 | 0/9 |
| ema_cross_slots1_risk1_tp2R | +7.38% | +10.43% | +3.95% | -3.53% | 12.14% | 105 | 2/9 |
| breakout30_slots1_risk1_tp2R | +14.44% | +28.34% | +17.22% | +17.70% | 8.29% | 138 | 1/9 |
| rsi2_only | +5.83% | +11.75% | +11.18% | +9.54% | 5.20% | 52 | 2/9 |

## Subsets — later period (all-in 32bps per side)
| Candidate | Original 12 | Other group A | Other group B |
|---|---:|---:|---:|
| breakout30_slots1_risk0.5_tp3R | -2.38% | +11.04% | -5.05% |
| breakout60_slots1_risk1_tp3R | -3.16% | +19.30% | +6.87% |
| range_breakout_slots1_risk0.5_tp2R | -1.38% | +4.04% | +6.18% |
| pullback_reclaim_slots1_risk1_tp2R | -6.51% | +4.32% | -2.00% |
| ema_cross_slots1_risk1_tp2R | +0.51% | +4.61% | -0.02% |
| breakout30_slots1_risk1_tp2R | -3.57% | +7.38% | -11.79% |
| rsi2_only | +0.13% | +7.10% | -0.89% |

## Research interpretation

Subset positivity counts are sensitivity diagnostics, not independent samples or a statistical significance test.
The coin lists remain selected by later survival and historical windows are reused.
Do not send live orders on the basis of this matrix.
The underlying same-account replay uses OHLC stop-first and simplified funding/cross liquidation.
A genuine untouched forward period must be collected after parameter selection.
