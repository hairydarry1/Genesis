# Forward T-C — prospective cluster-skip filter

## Method
Forward rule: at the close of a discharge spike bar i, enter at i+1 ONLY IF fewer than K adverse spikes have occurred in the trailing W M1 bars [i-W, i). K in {2, 3}; W in {10, 15, 20}.
Control: matched-lag random entries on the post-filter entry set (same entry-population effect).
OOS: halves by time. Reverse-time sanity check on W=10, K=2 (the central case).
Comparator: full e+1 set (no filter) and the retrospective non-cluster-start subset from the oracle test (ceiling).
Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors, 2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.

### crash300
- full e+1: avg=0.267 (WR 0.66, n=5341) ctrl=0.017 diff=0.250
- oracle non-cluster-start: avg=0.434 (n=4571) ctrl=-0.087 diff=0.521 (ceiling)
- oracle cluster-start: avg=-0.722 (n=770)

| config | avg | WR | n | skipped | ctrl | diff |
|---|---|---|---|---|---|---|
| W=10 K=2 | 0.238 | 0.67 | 1629 | 3712 | 0.478 | -0.240 |
| W=10 K=3 | 0.265 | 0.67 | 3715 | 1626 | 0.281 | -0.015 |
| W=15 K=2 | 0.160 | 0.66 | 839 | 4502 | 0.606 | -0.446 |
| W=15 K=3 | 0.315 | 0.67 | 2518 | 2823 | 0.514 | -0.199 |
| W=20 K=2 | 0.113 | 0.66 | 429 | 4912 | 0.865 | -0.753 |
| W=20 K=3 | 0.227 | 0.66 | 1574 | 3767 | 0.674 | -0.447 |
| W10_K2 OOS H1 | 0.274 | — | 805 | — | 0.550 | -0.276 |
| W10_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| W15_K2 OOS H1 | 0.184 | — | 420 | — | 0.877 | -0.693 |
| W15_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| reverse-time W=10 K=2 | -0.135 | — | 1624 | — | -0.492 | 0.356 | (sanity) |

### crash500
- full e+1: avg=0.126 (WR 0.74, n=3903) ctrl=-0.037 diff=0.162
- oracle non-cluster-start: avg=0.247 (n=3359) ctrl=-0.100 diff=0.347 (ceiling)
- oracle cluster-start: avg=-0.624 (n=544)

| config | avg | WR | n | skipped | ctrl | diff |
|---|---|---|---|---|---|---|
| W=10 K=2 | 0.110 | 0.73 | 1651 | 2252 | 0.225 | -0.115 |
| W=10 K=3 | 0.130 | 0.74 | 3140 | 763 | 0.057 | 0.073 |
| W=15 K=2 | 0.150 | 0.75 | 1035 | 2868 | 0.314 | -0.164 |
| W=15 K=3 | 0.126 | 0.74 | 2474 | 1429 | 0.161 | -0.035 |
| W=20 K=2 | 0.071 | 0.73 | 656 | 3247 | 0.448 | -0.377 |
| W=20 K=3 | 0.132 | 0.74 | 1869 | 2034 | 0.295 | -0.163 |
| W10_K2 OOS H1 | -0.013 | — | 798 | — | 0.272 | -0.285 |
| W10_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| W15_K2 OOS H1 | 0.001 | — | 528 | — | 0.381 | -0.380 |
| W15_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| reverse-time W=10 K=2 | -0.185 | — | 1651 | — | -0.275 | 0.090 | (sanity) |

### crash1000
- full e+1: avg=0.192 (WR 0.86, n=2208) ctrl=-0.077 diff=0.269
- oracle non-cluster-start: avg=0.353 (n=1989) ctrl=-0.272 diff=0.625 (ceiling)
- oracle cluster-start: avg=-1.272 (n=219)

| config | avg | WR | n | skipped | ctrl | diff |
|---|---|---|---|---|---|---|
| W=10 K=2 | 0.184 | 0.85 | 1398 | 810 | -0.021 | 0.205 |
| W=10 K=3 | 0.173 | 0.85 | 2049 | 159 | 0.062 | 0.111 |
| W=15 K=2 | 0.147 | 0.85 | 1073 | 1135 | 0.082 | 0.064 |
| W=15 K=3 | 0.157 | 0.85 | 1878 | 330 | 0.012 | 0.145 |
| W=20 K=2 | 0.144 | 0.85 | 830 | 1378 | 0.212 | -0.067 |
| W=20 K=3 | 0.164 | 0.85 | 1682 | 526 | 0.106 | 0.058 |
| W10_K2 OOS H1 | 0.157 | — | 709 | — | -0.086 | 0.243 |
| W10_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| W15_K2 OOS H1 | 0.108 | — | 534 | — | 0.075 | 0.033 |
| W15_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| reverse-time W=10 K=2 | -0.319 | — | 1397 | — | -0.376 | 0.057 | (sanity) |

### boom500
- full e+1: avg=0.154 (WR 0.73, n=3810) ctrl=0.076 diff=0.077
- oracle non-cluster-start: avg=0.329 (n=3240) ctrl=-0.118 diff=0.447 (ceiling)
- oracle cluster-start: avg=-0.842 (n=570)

| config | avg | WR | n | skipped | ctrl | diff |
|---|---|---|---|---|---|---|
| W=10 K=2 | 0.146 | 0.73 | 1622 | 2188 | 0.290 | -0.144 |
| W=10 K=3 | 0.159 | 0.73 | 3069 | 741 | 0.126 | 0.034 |
| W=15 K=2 | 0.099 | 0.72 | 1036 | 2774 | 0.365 | -0.266 |
| W=15 K=3 | 0.161 | 0.73 | 2425 | 1385 | 0.327 | -0.166 |
| W=20 K=2 | 0.103 | 0.72 | 637 | 3173 | 0.532 | -0.428 |
| W=20 K=3 | 0.117 | 0.72 | 1846 | 1964 | 0.193 | -0.077 |
| W10_K2 OOS H1 | 0.243 | — | 809 | — | 0.287 | -0.044 |
| W10_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| W15_K2 OOS H1 | 0.254 | — | 519 | — | 0.611 | -0.356 |
| W15_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| reverse-time W=10 K=2 | -0.236 | — | 1623 | — | -0.350 | 0.113 | (sanity) |

### boom1000
- full e+1: avg=0.310 (WR 0.85, n=2151) ctrl=-0.432 diff=0.743
- oracle non-cluster-start: avg=0.705 (n=1928) ctrl=-0.518 diff=1.222 (ceiling)
- oracle cluster-start: avg=-3.099 (n=223)

| config | avg | WR | n | skipped | ctrl | diff |
|---|---|---|---|---|---|---|
| W=10 K=2 | 0.324 | 0.86 | 1330 | 821 | -0.208 | 0.532 |
| W=10 K=3 | 0.254 | 0.85 | 1982 | 169 | -0.415 | 0.668 |
| W=15 K=2 | 0.251 | 0.85 | 1020 | 1131 | -0.358 | 0.609 |
| W=15 K=3 | 0.260 | 0.85 | 1811 | 340 | -0.466 | 0.726 |
| W=20 K=2 | 0.259 | 0.85 | 790 | 1361 | 0.493 | -0.233 |
| W=20 K=3 | 0.182 | 0.85 | 1625 | 526 | -0.434 | 0.616 |
| W10_K2 OOS H1 | 0.216 | — | 661 | — | 0.138 | 0.078 |
| W10_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| W15_K2 OOS H1 | 0.200 | — | 510 | — | 0.225 | -0.025 |
| W15_K2 OOS H2 | 0.000 | — | 0 | — | 0.000 | 0.000 |
| reverse-time W=10 K=2 | -0.460 | — | 1331 | — | -0.518 | 0.059 | (sanity) |

## Interpretation guide
- The forward filter's value should be judged relative to the oracle non-cluster-start ceiling,
  not relative to the full e+1 set. The full-set diff is confounded by the entry-population effect
  (the filter removes cluster-start entries, which are bad for reasons that may or may not be the
  same as the filter's causal claim). The matched-lag control on the post-filter set is the cleaner
  comparison: it asks whether the remaining entries are good because of the filter or because of
  the population they happen to be in.
- If the forward diff tracks the oracle diff across W/K and both OOS halves are positive and similar,
  the filter is live-able as a pre-entry rule.
- If the forward diff collapses toward zero while the oracle diff stays large, the signal is mostly
  retrospective (you only know it's a cluster after the fact) and the reactive cooldown is the right
  implementation.
- The reverse-time diff should be near zero; if it's large, the forward result is likely a
  level/statistics artifact rather than a temporal one (same discipline as the level-touch audit).
