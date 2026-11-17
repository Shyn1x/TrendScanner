# One-batch multi-strategy research — fixed assets and risk

Run: https://github.com/Shyn1x/TrendScanner/actions/runs/44162709719

43 scenarios: 5 independently defined 4H signal families
with 1/2 companion slots, 0.5/1% companion stop risk and 2R/3R exits;
plus RSI-only, breakout-only and unrestricted combined controls.
Same 100 survivorship-selected symbols and 2026-11-17 historical cutoff.

## All variants, ranked by min(train,later) return minus 0.5 × worst drawdown
| Scenario | Early return | Early DD | Early trades | Later return | Later DD | Later trades |
|---|---:|---:|---:|---:|---:|---:|
| breakout30_slots1_risk1_tp2R | +14.438% | 12.590% | 161 | +28.339% | 7.716% | 151 |
| breakout30_only | +14.414% | 14.877% | 305 | +18.345% | 11.755% | 302 |
| breakout30_slots1_risk0.5_tp3R | +11.762% | 10.892% | 164 | +13.676% | 5.893% | 139 |
| breakout60_slots1_risk1_tp3R | +17.126% | 13.519% | 154 | +11.165% | 7.621% | 138 |
| breakout30_slots1_risk0.5_tp2R | +10.158% | 10.473% | 165 | +9.508% | 5.893% | 143 |
| pullback_reclaim_slots1_risk1_tp2R | +8.869% | 10.125% | 145 | +16.031% | 6.303% | 130 |
| pullback_reclaim_slots1_risk1_tp3R | +8.316% | 11.265% | 142 | +9.636% | 8.669% | 126 |
| breakout30_slots1_risk1_tp3R | +16.569% | 14.941% | 162 | +9.836% | 7.831% | 144 |
| ema_cross_slots1_risk1_tp3R | +15.358% | 9.511% | 128 | +7.184% | 10.081% | 110 |
| ema_cross_slots1_risk1_tp2R | +7.379% | 10.803% | 130 | +10.434% | 7.309% | 104 |
| range_breakout_slots1_risk0.5_tp2R | +12.284% | 13.624% | 147 | +8.523% | 5.534% | 124 |
| range_breakout_slots2_risk0.5_tp2R | +8.223% | 13.071% | 198 | +10.977% | 5.683% | 174 |
| range_breakout_slots1_risk1_tp3R | +13.177% | 17.924% | 139 | +10.596% | 8.618% | 122 |
| breakout60_slots2_risk1_tp3R | +9.385% | 16.926% | 207 | +13.899% | 13.857% | 181 |
| rsi2_only | +5.834% | 10.412% | 59 | +11.753% | 4.943% | 53 |
| breakout30_slots2_risk1_tp2R | +8.249% | 14.555% | 227 | +7.927% | 15.614% | 200 |
| breakout60_slots1_risk0.5_tp3R | +5.435% | 10.880% | 157 | +15.269% | 5.810% | 136 |
| pullback_reclaim_slots1_risk0.5_tp2R | +4.212% | 8.941% | 149 | +10.386% | 6.279% | 130 |
| breakout60_slots1_risk0.5_tp2R | +3.796% | 9.697% | 157 | +17.176% | 6.599% | 141 |
| range_breakout_slots1_risk0.5_tp3R | +8.515% | 14.009% | 142 | +5.456% | 5.945% | 125 |
| ema_cross_slots1_risk0.5_tp3R | +11.708% | 8.987% | 132 | +2.685% | 8.239% | 111 |
| pullback_reclaim_slots1_risk0.5_tp3R | +3.056% | 9.959% | 144 | +11.158% | 6.648% | 128 |
| breakout30_slots2_risk0.5_tp2R | +5.354% | 9.719% | 229 | +3.580% | 11.026% | 201 |
| pullback_reclaim_slots2_risk0.5_tp3R | +2.585% | 10.227% | 204 | +6.966% | 6.088% | 187 |
| pullback_reclaim_slots2_risk0.5_tp2R | +3.517% | 12.214% | 210 | +7.160% | 6.088% | 185 |
| ema_cross_slots1_risk0.5_tp2R | +4.707% | 8.347% | 130 | +1.447% | 8.239% | 111 |
| breakout60_slots1_risk1_tp2R | +3.377% | 12.440% | 156 | +15.794% | 10.759% | 143 |
| breakout60_slots2_risk0.5_tp3R | +2.551% | 13.031% | 210 | +4.279% | 7.853% | 183 |
| breakout60_slots2_risk0.5_tp2R | +3.455% | 10.391% | 213 | +0.907% | 7.317% | 192 |
| range_breakout_slots2_risk1_tp2R | +5.149% | 19.960% | 196 | +16.198% | 8.067% | 173 |
| pullback_reclaim_slots2_risk1_tp3R | +2.271% | 14.617% | 208 | +23.240% | 7.798% | 169 |
| breakout30_slots2_risk0.5_tp3R | +3.885% | 11.152% | 222 | +0.254% | 9.761% | 202 |
| combined_unrestricted | +3.635% | 17.921% | 253 | +6.033% | 14.217% | 229 |
| range_breakout_slots2_risk1_tp3R | +5.145% | 19.659% | 193 | +4.397% | 12.243% | 172 |
| range_breakout_slots2_risk0.5_tp3R | +5.833% | 13.876% | 196 | +0.995% | 8.277% | 177 |
| range_breakout_slots1_risk1_tp2R | +2.887% | 17.904% | 142 | +11.417% | 7.149% | 122 |
| ema_cross_slots2_risk0.5_tp3R | +8.484% | 8.334% | 161 | -2.612% | 9.693% | 141 |
| ema_cross_slots2_risk0.5_tp2R | +3.370% | 8.334% | 163 | -3.611% | 9.355% | 140 |
| ema_cross_slots2_risk1_tp2R | +3.920% | 11.234% | 159 | -5.294% | 11.691% | 140 |
| pullback_reclaim_slots2_risk1_tp2R | -2.653% | 18.240% | 209 | +5.751% | 8.291% | 180 |
| breakout60_slots2_risk1_tp2R | +1.962% | 17.074% | 210 | -5.862% | 19.123% | 184 |
| ema_cross_slots2_risk1_tp3R | +6.108% | 11.557% | 158 | -9.474% | 14.739% | 137 |
| breakout30_slots2_risk1_tp3R | +5.551% | 15.617% | 216 | -9.695% | 21.146% | 202 |

## Research screen (not proof)

Pass = positive on both reused windows, late DD no more than baseline +3pp,
and later trade count above RSI2-only. Passing variants: 20.
- breakout30_slots1_risk1_tp2R: earlier +14.44%, later +28.34%, later DD 7.72%, 151 trades
- breakout30_slots1_risk0.5_tp3R: earlier +11.76%, later +13.68%, later DD 5.89%, 139 trades
- breakout60_slots1_risk1_tp3R: earlier +17.13%, later +11.16%, later DD 7.62%, 138 trades
- breakout30_slots1_risk0.5_tp2R: earlier +10.16%, later +9.51%, later DD 5.89%, 143 trades
- pullback_reclaim_slots1_risk1_tp2R: earlier +8.87%, later +16.03%, later DD 6.30%, 130 trades
- breakout30_slots1_risk1_tp3R: earlier +16.57%, later +9.84%, later DD 7.83%, 144 trades
- ema_cross_slots1_risk1_tp2R: earlier +7.38%, later +10.43%, later DD 7.31%, 104 trades
- range_breakout_slots1_risk0.5_tp2R: earlier +12.28%, later +8.52%, later DD 5.53%, 124 trades
- range_breakout_slots2_risk0.5_tp2R: earlier +8.22%, later +10.98%, later DD 5.68%, 174 trades
- breakout60_slots1_risk0.5_tp3R: earlier +5.43%, later +15.27%, later DD 5.81%, 136 trades

## Limitations

*Exploratory in-sample selection over already reused historical windows.
Not a portfolio/live recommendation. Same OHLC stop-first, simulated cross
risk, survivorship-biased universe, and hypothetical funding assumptions.*
