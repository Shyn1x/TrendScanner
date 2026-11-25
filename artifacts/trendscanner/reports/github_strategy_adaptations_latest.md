# GitHub-inspired strategy adaptations batch — 123 scenarios (not source-exact)

Run: https://github.com/Shyn1x/TrendScanner/actions/runs/45587868590

123 scenarios: 15 different 4H signal families, 10 adapted from public code concepts
with 1/2 companion slots, 0.5/1% companion stop risk and 2R/3R exits;
plus RSI-only, breakout-only and unrestricted combined controls.
Same 100 survivorship-selected symbols and 2026-11-17 historical cutoff.

## All variants, ranked by min(train,later) return minus 0.5 × worst drawdown
| Scenario | Early return | Early DD | Early trades | Later return | Later DD | Later trades |
|---|---:|---:|---:|---:|---:|---:|
| breakout30_slots1_risk1_tp2R | +14.438% | 12.590% | 161 | +28.339% | 7.716% | 151 |
| tr_pullback_slots2_risk0.5_tp3R | +12.787% | 10.408% | 257 | +14.591% | 4.322% | 236 |
| breakout30_only | +14.414% | 14.877% | 305 | +18.345% | 11.755% | 302 |
| breakout30_slots1_risk0.5_tp3R | +11.762% | 10.892% | 164 | +13.676% | 5.893% | 139 |
| ft_rsi_revert_slots2_risk0.5_tp2R | +9.613% | 8.889% | 74 | +12.031% | 4.698% | 61 |
| ft_rsi_revert_slots2_risk0.5_tp3R | +9.613% | 8.889% | 74 | +12.031% | 4.698% | 61 |
| tr_pullback_slots1_risk1_tp3R | +12.013% | 14.408% | 165 | +23.941% | 5.838% | 150 |
| breakout60_slots1_risk1_tp3R | +17.126% | 13.519% | 154 | +11.165% | 7.621% | 138 |
| breakout30_slots1_risk0.5_tp2R | +10.158% | 10.473% | 165 | +9.508% | 5.893% | 143 |
| ft_rsi_revert_slots1_risk1_tp2R | +8.944% | 9.368% | 74 | +10.631% | 4.834% | 60 |
| ft_rsi_revert_slots1_risk1_tp3R | +8.944% | 9.368% | 74 | +10.631% | 4.834% | 60 |
| ft_rsi_revert_slots1_risk0.5_tp2R | +8.772% | 9.536% | 73 | +12.288% | 4.482% | 60 |
| ft_rsi_revert_slots1_risk0.5_tp3R | +8.772% | 9.536% | 73 | +12.288% | 4.482% | 60 |
| pullback_reclaim_slots1_risk1_tp2R | +8.869% | 10.125% | 145 | +16.031% | 6.303% | 130 |
| ft_rsi_revert_slots2_risk1_tp2R | +8.492% | 10.285% | 76 | +12.509% | 4.616% | 61 |
| ft_rsi_revert_slots2_risk1_tp3R | +8.492% | 10.285% | 76 | +12.509% | 4.616% | 61 |
| tr_pullback_slots1_risk0.5_tp3R | +8.808% | 11.673% | 169 | +19.820% | 3.105% | 152 |
| tr_pullback_slots1_risk1_tp2R | +9.804% | 13.864% | 165 | +24.222% | 5.323% | 150 |
| pullback_reclaim_slots1_risk1_tp3R | +8.316% | 11.265% | 142 | +9.636% | 8.669% | 126 |
| tr_pullback_slots2_risk0.5_tp2R | +7.530% | 9.902% | 259 | +16.310% | 4.908% | 236 |
| tr_pullback_slots2_risk1_tp3R | +9.457% | 14.051% | 251 | +15.308% | 9.155% | 234 |
| breakout30_slots1_risk1_tp3R | +16.569% | 14.941% | 162 | +9.836% | 7.831% | 144 |
| ema_cross_slots1_risk1_tp3R | +15.358% | 9.511% | 128 | +7.184% | 10.081% | 110 |
| ema_cross_slots1_risk1_tp2R | +7.379% | 10.803% | 130 | +10.434% | 7.309% | 104 |
| range_breakout_slots1_risk0.5_tp2R | +12.284% | 13.624% | 147 | +8.523% | 5.534% | 124 |
| range_breakout_slots2_risk0.5_tp2R | +8.223% | 13.071% | 198 | +10.977% | 5.683% | 174 |
| ft_bb_revert_slots1_risk0.5_tp2R | +8.046% | 7.571% | 128 | +5.464% | 4.864% | 113 |
| range_breakout_slots1_risk1_tp3R | +13.177% | 17.924% | 139 | +10.596% | 8.618% | 122 |
| ft_ema_trend_slots1_risk0.5_tp3R | +10.007% | 8.675% | 121 | +5.965% | 7.431% | 105 |
| ft_ema_trend_slots2_risk1_tp3R | +9.060% | 10.650% | 145 | +6.271% | 9.189% | 131 |
| breakout60_slots2_risk1_tp3R | +9.385% | 16.926% | 207 | +13.899% | 13.857% | 181 |
| ft_bb_revert_slots1_risk0.5_tp3R | +9.395% | 8.740% | 128 | +4.999% | 5.285% | 114 |
| rsi2_only | +5.834% | 10.412% | 59 | +11.753% | 4.943% | 53 |
| ft_ema_trend_slots2_risk0.5_tp3R | +6.776% | 9.694% | 146 | +5.454% | 7.539% | 134 |
| breakout30_slots2_risk1_tp2R | +8.249% | 14.555% | 227 | +7.927% | 15.614% | 200 |
| breakout60_slots1_risk0.5_tp3R | +5.435% | 10.880% | 157 | +15.269% | 5.810% | 136 |
| ft_macd_bull_slots1_risk1_tp3R | +7.236% | 14.524% | 145 | +13.314% | 6.731% | 125 |
| ft_bb_revert_slots2_risk0.5_tp3R | +3.967% | 8.315% | 165 | +10.947% | 5.280% | 154 |
| ft_ema_trend_slots2_risk0.5_tp2R | +7.528% | 8.806% | 148 | +4.191% | 7.539% | 133 |
| pullback_reclaim_slots1_risk0.5_tp2R | +4.212% | 8.941% | 149 | +10.386% | 6.279% | 130 |
| tr_pullback_slots1_risk0.5_tp2R | +5.448% | 11.774% | 171 | +14.147% | 4.347% | 155 |
| ft_bb_revert_slots2_risk0.5_tp2R | +3.512% | 8.285% | 164 | +3.304% | 6.293% | 157 |
| tr_ema_cross_slots1_risk1_tp3R | +5.341% | 12.378% | 140 | +20.607% | 7.473% | 133 |
| breakout60_slots1_risk0.5_tp2R | +3.796% | 9.697% | 157 | +17.176% | 6.599% | 141 |
| ft_bb_revert_slots1_risk1_tp3R | +14.615% | 9.315% | 128 | +3.594% | 6.126% | 112 |
| range_breakout_slots1_risk0.5_tp3R | +8.515% | 14.009% | 142 | +5.456% | 5.945% | 125 |
| tr_rsi_bounce_slots1_risk0.5_tp3R | +3.567% | 10.607% | 95 | +4.727% | 3.427% | 77 |
| ema_cross_slots1_risk0.5_tp3R | +11.708% | 8.987% | 132 | +2.685% | 8.239% | 111 |
| pullback_reclaim_slots1_risk0.5_tp3R | +3.056% | 9.959% | 144 | +11.158% | 6.648% | 128 |
| breakout30_slots2_risk0.5_tp2R | +5.354% | 9.719% | 229 | +3.580% | 11.026% | 201 |
| ft_ema_trend_slots2_risk1_tp2R | +3.990% | 12.006% | 148 | +4.147% | 8.982% | 132 |
| ft_bb_revert_slots1_risk1_tp2R | +10.167% | 10.408% | 130 | +3.058% | 6.126% | 113 |
| tr_ema_cross_slots1_risk0.5_tp3R | +3.454% | 11.571% | 144 | +17.103% | 4.410% | 130 |
| ft_macd_bull_slots1_risk0.5_tp3R | +4.105% | 13.205% | 146 | +5.859% | 5.681% | 124 |
| pullback_reclaim_slots2_risk0.5_tp3R | +2.585% | 10.227% | 204 | +6.966% | 6.088% | 187 |
| tr_macd_cross_slots1_risk0.5_tp2R | +3.701% | 12.570% | 147 | +12.146% | 6.122% | 118 |
| pullback_reclaim_slots2_risk0.5_tp2R | +3.517% | 12.214% | 210 | +7.160% | 6.088% | 185 |
| ema_cross_slots1_risk0.5_tp2R | +4.707% | 8.347% | 130 | +1.447% | 8.239% | 111 |
| tr_macd_cross_slots1_risk0.5_tp3R | +3.018% | 11.649% | 142 | +17.373% | 4.117% | 119 |
| breakout60_slots1_risk1_tp2R | +3.377% | 12.440% | 156 | +15.794% | 10.759% | 143 |
| tr_rsi_bounce_slots1_risk0.5_tp2R | +2.383% | 10.612% | 95 | +6.267% | 3.934% | 76 |
| ft_ema_trend_slots1_risk0.5_tp2R | +6.695% | 8.146% | 121 | +0.933% | 7.431% | 104 |
| tr_bb_bounce_slots1_risk1_tp2R | +2.225% | 11.339% | 128 | +11.419% | 4.917% | 103 |
| breakout60_slots2_risk0.5_tp3R | +2.551% | 13.031% | 210 | +4.279% | 7.853% | 183 |
| tr_rsi_bounce_slots1_risk1_tp2R | +2.143% | 12.589% | 98 | +6.980% | 3.536% | 77 |
| tr_ema_cross_slots1_risk0.5_tp2R | +1.516% | 11.423% | 143 | +10.492% | 4.544% | 133 |
| breakout60_slots2_risk0.5_tp2R | +3.455% | 10.391% | 213 | +0.907% | 7.317% | 192 |
| tr_rsi_bounce_slots2_risk0.5_tp2R | +0.457% | 10.290% | 112 | +4.107% | 4.315% | 89 |
| tr_ema_cross_slots1_risk1_tp2R | +2.019% | 13.430% | 139 | +10.101% | 10.320% | 129 |
| range_breakout_slots2_risk1_tp2R | +5.149% | 19.960% | 196 | +16.198% | 8.067% | 173 |
| pullback_reclaim_slots2_risk1_tp3R | +2.271% | 14.617% | 208 | +23.240% | 7.798% | 169 |
| tr_ema_cross_slots2_risk1_tp3R | +2.691% | 15.767% | 200 | +6.487% | 10.881% | 185 |
| tr_macd_cross_slots2_risk0.5_tp3R | +0.413% | 11.211% | 193 | +8.956% | 6.420% | 159 |
| tr_macd_cross_slots1_risk1_tp3R | +1.343% | 13.132% | 143 | +21.590% | 7.959% | 121 |
| tr_rsi_bounce_slots1_risk1_tp3R | +0.984% | 12.546% | 97 | +8.080% | 3.479% | 77 |
| breakout30_slots2_risk0.5_tp3R | +3.885% | 11.152% | 222 | +0.254% | 9.761% | 202 |
| combined_unrestricted | +3.635% | 17.921% | 253 | +6.033% | 14.217% | 229 |
| range_breakout_slots2_risk1_tp3R | +5.145% | 19.659% | 193 | +4.397% | 12.243% | 172 |
| tr_bb_bounce_slots1_risk1_tp3R | +0.235% | 11.365% | 128 | +7.611% | 6.242% | 103 |
| tr_ema_cross_slots2_risk0.5_tp3R | +0.363% | 11.842% | 203 | +8.085% | 7.250% | 185 |
| ft_macd_bull_slots1_risk0.5_tp2R | +0.271% | 12.380% | 143 | +5.382% | 6.637% | 126 |
| range_breakout_slots2_risk0.5_tp3R | +5.833% | 13.876% | 196 | +0.995% | 8.277% | 177 |
| range_breakout_slots1_risk1_tp2R | +2.887% | 17.904% | 142 | +11.417% | 7.149% | 122 |
| ft_macd_bull_slots2_risk0.5_tp3R | +1.656% | 16.199% | 191 | +2.489% | 5.590% | 167 |
| tr_ema_cross_slots2_risk0.5_tp2R | -0.920% | 11.059% | 202 | +4.582% | 8.776% | 188 |
| ft_ema_trend_slots1_risk1_tp3R | +10.805% | 10.113% | 122 | -1.347% | 10.259% | 105 |
| tr_bb_bounce_slots1_risk0.5_tp2R | -1.685% | 10.192% | 128 | +8.926% | 3.887% | 105 |
| tr_rsi_bounce_slots2_risk0.5_tp3R | -1.663% | 10.267% | 113 | +5.925% | 4.315% | 86 |
| tr_bb_bounce_slots1_risk0.5_tp3R | -1.928% | 10.218% | 128 | +3.692% | 5.430% | 103 |
| tr_bb_bounce_slots2_risk0.5_tp2R | -1.825% | 10.637% | 167 | +6.663% | 4.919% | 125 |
| tr_ema_cross_slots2_risk1_tp2R | +0.620% | 15.655% | 201 | +4.903% | 10.694% | 184 |
| ema_cross_slots2_risk0.5_tp3R | +8.484% | 8.334% | 161 | -2.612% | 9.693% | 141 |
| ft_macd_bull_slots2_risk1_tp3R | +2.181% | 19.562% | 189 | +5.241% | 8.425% | 164 |
| tr_macd_cross_slots2_risk0.5_tp2R | -1.434% | 12.575% | 196 | +10.557% | 7.556% | 159 |
| ft_ema_trend_slots1_risk1_tp2R | +7.847% | 10.067% | 121 | -2.842% | 10.259% | 105 |
| ema_cross_slots2_risk0.5_tp2R | +3.370% | 8.334% | 163 | -3.611% | 9.355% | 140 |
| tr_macd_cross_slots1_risk1_tp2R | -1.144% | 14.550% | 141 | +17.142% | 6.368% | 122 |
| ft_bb_revert_slots2_risk1_tp2R | -2.496% | 11.959% | 164 | +7.900% | 8.018% | 152 |
| ft_macd_bull_slots2_risk0.5_tp2R | -1.365% | 14.695% | 192 | +5.218% | 5.867% | 171 |
| tr_macd_cross_slots2_risk1_tp2R | -0.930% | 15.784% | 186 | +7.864% | 9.297% | 155 |
| tr_bb_bounce_slots2_risk1_tp2R | -2.570% | 13.026% | 162 | +9.386% | 5.728% | 122 |
| ft_bb_revert_slots2_risk1_tp3R | -2.543% | 13.253% | 162 | +11.148% | 7.745% | 153 |
| tr_bb_bounce_slots2_risk0.5_tp3R | -3.672% | 11.325% | 169 | +6.051% | 5.022% | 126 |
| ft_macd_bull_slots1_risk1_tp2R | -1.057% | 18.285% | 146 | +15.526% | 5.927% | 131 |
| tr_ema50_bounce_slots1_risk0.5_tp2R | -4.447% | 13.036% | 127 | +10.684% | 7.719% | 110 |
| tr_pullback_slots2_risk1_tp2R | -0.695% | 20.827% | 257 | +20.603% | 5.785% | 241 |
| ema_cross_slots2_risk1_tp2R | +3.920% | 11.234% | 159 | -5.294% | 11.691% | 140 |
| tr_bb_bounce_slots2_risk1_tp3R | -4.311% | 13.859% | 166 | +8.833% | 6.111% | 123 |
| tr_macd_cross_slots2_risk1_tp3R | -3.569% | 15.753% | 187 | +14.151% | 7.518% | 158 |
| pullback_reclaim_slots2_risk1_tp2R | -2.653% | 18.240% | 209 | +5.751% | 8.291% | 180 |
| tr_rsi_bounce_slots2_risk1_tp3R | -6.362% | 12.086% | 108 | +8.853% | 5.518% | 84 |
| tr_rsi_bounce_slots2_risk1_tp2R | -6.543% | 12.129% | 109 | +7.671% | 5.518% | 85 |
| tr_ema50_bounce_slots2_risk0.5_tp2R | -5.760% | 13.814% | 173 | +9.155% | 9.153% | 140 |
| tr_ema50_bounce_slots1_risk1_tp2R | -4.844% | 16.008% | 128 | +6.266% | 11.146% | 110 |
| tr_ema50_bounce_slots1_risk0.5_tp3R | -6.161% | 14.181% | 128 | +12.557% | 7.150% | 114 |
| tr_ema50_bounce_slots2_risk0.5_tp3R | -6.543% | 14.733% | 173 | +11.165% | 7.480% | 140 |
| tr_ema50_bounce_slots1_risk1_tp3R | -6.306% | 17.299% | 128 | +7.520% | 10.394% | 109 |
| breakout60_slots2_risk1_tp2R | +1.962% | 17.074% | 210 | -5.862% | 19.123% | 184 |
| tr_ema50_bounce_slots2_risk1_tp2R | -6.102% | 19.106% | 170 | +5.032% | 14.602% | 139 |
| ema_cross_slots2_risk1_tp3R | +6.108% | 11.557% | 158 | -9.474% | 14.739% | 137 |
| ft_macd_bull_slots2_risk1_tp2R | -7.575% | 23.197% | 186 | +8.683% | 6.160% | 168 |
| breakout30_slots2_risk1_tp3R | +5.551% | 15.617% | 216 | -9.695% | 21.146% | 202 |
| tr_ema50_bounce_slots2_risk1_tp3R | -11.190% | 22.035% | 172 | +5.907% | 11.577% | 139 |

## Research screen (not proof)

Pass = positive on both reused windows, late DD no more than baseline +3pp,
and later trade count above RSI2-only. Passing variants: 62.
- breakout30_slots1_risk1_tp2R: earlier +14.44%, later +28.34%, later DD 7.72%, 151 trades
- tr_pullback_slots2_risk0.5_tp3R: earlier +12.79%, later +14.59%, later DD 4.32%, 236 trades
- breakout30_slots1_risk0.5_tp3R: earlier +11.76%, later +13.68%, later DD 5.89%, 139 trades
- ft_rsi_revert_slots2_risk0.5_tp2R: earlier +9.61%, later +12.03%, later DD 4.70%, 61 trades
- ft_rsi_revert_slots2_risk0.5_tp3R: earlier +9.61%, later +12.03%, later DD 4.70%, 61 trades
- tr_pullback_slots1_risk1_tp3R: earlier +12.01%, later +23.94%, later DD 5.84%, 150 trades
- breakout60_slots1_risk1_tp3R: earlier +17.13%, later +11.16%, later DD 7.62%, 138 trades
- breakout30_slots1_risk0.5_tp2R: earlier +10.16%, later +9.51%, later DD 5.89%, 143 trades
- ft_rsi_revert_slots1_risk1_tp2R: earlier +8.94%, later +10.63%, later DD 4.83%, 60 trades
- ft_rsi_revert_slots1_risk1_tp3R: earlier +8.94%, later +10.63%, later DD 4.83%, 60 trades

## Limitations

**Important:** 4H conceptual adaptations of TrendRider public 1H entry categories and generic Freqtrade indicator strategies; NOT byte-for-byte original strategies. Original multi-timeframe, ADX, volume, OBV, ROI/exit, regime and execution are not reproduced.
Source concepts: https://github.com/darkvolg/Trading and https://github.com/freqtrade/freqtrade-strategies

*Exploratory in-sample selection over already reused historical windows.
Not a portfolio/live recommendation. Same OHLC stop-first, simulated cross
risk, survivorship-biased universe, and hypothetical funding assumptions.*
