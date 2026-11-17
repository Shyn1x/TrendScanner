# RSI2 $300 Cross — latest verified research artifact

Data through: **2026-11-17 00:00 UTC**.

Source: `rsi2-exit-risk-fee-capped-300-results.zip` supplied in the conversation. This is a **manual bootstrap** to verify reading through GitHub; automatic CI publishing is not yet verified.

| Strategy | Early return | Early DD | Later return | Later DD | Later trades |
|---|---:|---:|---:|---:|---:|
| 1D · 3 slots · no add · 3R | −1.633% | 12.201% | +16.977% | 8.087% | 75 |
| 1D · 3 slots · add · 3R | −1.633% | 12.201% | +16.977% | 8.087% | 75 |
| 1D · 3 slots · no add · 2R | −2.530% | 12.023% | +13.497% | 8.087% | 77 |
| 4H · 3 slots · no add · 3R | +10.664% | 8.326% | +7.375% | 10.883% | 105 |

Simulation assumptions: initial $300, 10x cross proxy, daily ATR×2.5 stop, 1% initial risk budget, 16 bps/side fees and hypothetical funding. Uses historical reused train/later and current surviving top-100 Bybit universe: not independent out-of-sample, not live-ready.

The GitHub Actions workflow `rsi2-exit-risk-fee-capped-300.yml` is configured to overwrite this report after a successful run. If the run cannot push, retain this last verified snapshot and inspect its logs.
