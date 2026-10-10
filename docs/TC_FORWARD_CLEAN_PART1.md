# Clean Forward T-C — unbiased random sub-sample control + cluster-label diagnostic

## Method
Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors, 2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.
Spike = adverse bar with range > 3x trailing-100 median. Crash family = LONG drift at e+1; Boom family = SHORT drift at e+1.
Forward filter: at spike close i, enter at i+1 only if fewer than K adverse spikes in trailing W M1 bars [i-W, i). K in {2, 3}; W in {10, 15, 20}.
Control: UNBIASED random sub-sample of the FULL e+1 set, same size as the filter set, multiple seeds (5). Compare filter avg to random sub-sample avg across seeds.
Cluster label (oracle): each spike labeled cluster-start (first of >=3 in 10 bars), cluster-member (in cluster but not first), or solo (not in any >=3 cluster).
Diagnostic: for each filter, removal rate per label, and filtered-set avg per label kept.

### crash300
- full e+1: avg=0.267 (solo=1.671 n=947, member=0.111 n=3624, start=-0.722 n=770)
- full-population label means: solo=1.671, member=0.111, start=-0.722

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1629 | 0.238 | 0.223 | [0.154, 0.302] | +0.016 | 0.283 | 0.950 | 0.000 |
| 10 | 3 | 3715 | 0.265 | 0.260 | [0.235, 0.292] | +0.005 | 0.000 | 0.449 | 0.000 |
| 15 | 2 | 839 | 0.160 | 0.224 | [0.137, 0.452] | -0.064 | 0.602 | 1.000 | 0.400 |
| 15 | 3 | 2518 | 0.315 | 0.248 | [0.201, 0.345] | +0.068 | 0.086 | 0.741 | 0.073 |
| 20 | 2 | 429 | 0.113 | 0.215 | [0.045, 0.426] | -0.103 | 0.805 | 1.000 | 0.683 |
| 20 | 3 | 1574 | 0.227 | 0.219 | [0.129, 0.305] | +0.008 | 0.373 | 0.874 | 0.321 |

#### Label kept-avg for each filter (W=10 K=2 unless noted)
- crash300 W=10 K=2: kept-avg solo=1.351, member=0.147, start=-0.722

### crash500
- full e+1: avg=0.126 (solo=0.665 n=1346, member=-0.032 n=2013, start=-0.624 n=544)
- full-population label means: solo=0.665, member=-0.032, start=-0.624

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1651 | 0.110 | 0.132 | [0.057, 0.175] | -0.021 | 0.267 | 0.940 | 0.000 |
| 10 | 3 | 3140 | 0.130 | 0.117 | [0.079, 0.146] | +0.012 | 0.000 | 0.379 | 0.000 |
| 15 | 2 | 1035 | 0.150 | 0.116 | [0.039, 0.184] | +0.034 | 0.509 | 1.000 | 0.312 |
| 15 | 3 | 2474 | 0.126 | 0.126 | [0.089, 0.153] | -0.001 | 0.049 | 0.666 | 0.042 |
| 20 | 2 | 656 | 0.071 | 0.125 | [0.053, 0.193] | -0.054 | 0.693 | 1.000 | 0.553 |
| 20 | 3 | 1869 | 0.132 | 0.134 | [0.076, 0.163] | -0.003 | 0.227 | 0.812 | 0.173 |

#### Label kept-avg for each filter (W=10 K=2 unless noted)
- crash500 W=10 K=2: kept-avg solo=0.533, member=-0.039, start=-0.624

### crash1000
- full e+1: avg=0.192 (solo=0.597 n=1432, member=-0.274 n=557, start=-1.272 n=219)
- full-population label means: solo=0.597, member=-0.274, start=-1.272

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1398 | 0.184 | 0.209 | [0.172, 0.246] | -0.026 | 0.199 | 0.943 | 0.000 |
| 10 | 3 | 2049 | 0.173 | 0.203 | [0.190, 0.213] | -0.031 | 0.000 | 0.285 | 0.000 |
| 15 | 2 | 1073 | 0.147 | 0.216 | [0.156, 0.260] | -0.069 | 0.377 | 1.000 | 0.174 |
| 15 | 3 | 1878 | 0.157 | 0.199 | [0.176, 0.231] | -0.041 | 0.020 | 0.533 | 0.018 |
| 20 | 2 | 830 | 0.144 | 0.183 | [0.135, 0.231] | -0.039 | 0.516 | 1.000 | 0.374 |
| 20 | 3 | 1682 | 0.164 | 0.207 | [0.152, 0.251] | -0.043 | 0.091 | 0.684 | 0.068 |

#### Label kept-avg for each filter (W=10 K=2 unless noted)
- crash1000 W=10 K=2: kept-avg solo=0.492, member=-0.895, start=-1.272

### boom500
- full e+1: avg=0.154 (solo=1.000 n=1262, member=-0.099 n=1978, start=-0.842 n=570)
- full-population label means: solo=1.000, member=-0.099, start=-0.842

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1622 | 0.146 | 0.181 | [0.090, 0.273] | -0.035 | 0.259 | 0.941 | 0.000 |
| 10 | 3 | 3069 | 0.159 | 0.157 | [0.132, 0.186] | +0.003 | 0.000 | 0.375 | 0.000 |
| 15 | 2 | 1036 | 0.099 | 0.184 | [0.042, 0.335] | -0.085 | 0.494 | 1.000 | 0.302 |
| 15 | 3 | 2425 | 0.161 | 0.147 | [0.111, 0.175] | +0.014 | 0.048 | 0.661 | 0.032 |
| 20 | 2 | 637 | 0.103 | 0.137 | [0.004, 0.281] | -0.034 | 0.689 | 1.000 | 0.570 |
| 20 | 3 | 1846 | 0.117 | 0.167 | [0.109, 0.243] | -0.050 | 0.219 | 0.794 | 0.204 |

#### Label kept-avg for each filter (W=10 K=2 unless noted)
- boom500 W=10 K=2: kept-avg solo=0.794, member=-0.221, start=-0.842

### boom1000
- full e+1: avg=0.310 (solo=1.345 n=1355, member=-0.810 n=573, start=-3.099 n=223)
- full-population label means: solo=1.345, member=-0.810, start=-3.099

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1330 | 0.324 | 0.315 | [0.232, 0.443] | +0.010 | 0.210 | 0.935 | 0.000 |
| 10 | 3 | 1982 | 0.254 | 0.317 | [0.287, 0.395] | -0.064 | 0.000 | 0.295 | 0.000 |
| 15 | 2 | 1020 | 0.251 | 0.342 | [0.197, 0.456] | -0.091 | 0.380 | 1.000 | 0.193 |
| 15 | 3 | 1811 | 0.260 | 0.293 | [0.200, 0.385] | -0.034 | 0.021 | 0.545 | 0.000 |
| 20 | 2 | 790 | 0.259 | 0.335 | [0.231, 0.522] | -0.075 | 0.528 | 1.000 | 0.323 |
| 20 | 3 | 1625 | 0.182 | 0.297 | [0.217, 0.405] | -0.114 | 0.098 | 0.661 | 0.063 |

#### Label kept-avg for each filter (W=10 K=2 unless noted)
- boom1000 W=10 K=2: kept-avg solo=1.099, member=-1.432, start=-3.099

## Interpretation
- clean diff = filter avg - random sub-sample mean. Positive = filter selects a better-than-random
  subset of the full population. The random sub-sample is unbiased (drawn uniformly from the full set,
  same size as the filter set), so this is the honest comparison.
- label removal rates: solo removal should be LOW (we want to keep solo entries), start removal should be
  HIGH (we want to remove cluster-start entries). member removal is the reactive skip's job, so the forward
  filter's member removal is a bonus if it happens (it means the filter also removes some cluster members),
  but it's not the primary target.
- label kept-avg: if the filter keeps solo entries with high avg and start entries with low avg, it's
  doing the right thing within the kept set. If kept-avg start is high, the filter is keeping bad entries.
