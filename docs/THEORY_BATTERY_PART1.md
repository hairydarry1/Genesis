# Theory Battery — T-A / T-B / T-C / T-D

## Method
Verified corrected engine (partial_m1_corrected: gap-through fills, half_exit partial add, per-symbol broker floors, 2xSL/1xSL, hold=3).
Spike = adverse bar with range > 3x trailing-100 median. Crash family = LONG drift (enter at e+1); Boom family = SHORT drift (enter at e+1).
M15 regime / BOS from closed-M15-bar discipline (no lookahead). OOS halves by time. Matched-lag controls.
All PnLs in points per trade (1-lot equiv); not dollars; no spread/commission.

NOTE: T-C cluster_start label is ORACLE (computed from full spike set). The forward-testable version ('skip if this spike AND already saw 2+ in trailing window') is a follow-up.

### crash300
- spikes=5341 (cluster-start=770, solo=4571)
- T-C: discharge-following e+1 avg=0.267 (WR 0.66), rand avg=0.153, diff=0.114
  - non-cluster-start entries avg=0.434 (n=4571) vs ctrl -0.016 -> diff 0.450
  - cluster-start entries avg=-0.722 (n=770)
- T-A: break-rate buckets (per-trade avg): {'0': 0.8468051771117016, '1': 0.37076018258426924, '2': 0.2369865911799799, '3+': 0.10192574786324984}; largest=3+ ctrl=-0.043
- T-B: bar1-fav buckets (per-trade avg): {'(0,0.46]': 0.6654385964912412, '(0.46,1.38]': 0.81642344606947, '<=0': -2.432634593993323, '>1.38': 0.39422222222214665} (med bar1 range=0.921)
- T-D: dense base avg=1.635 (n=8493), hard-mask 3-8 avg=1.571 (n=22), soft-skip 3+ avg=1.633 (n=8475), soft ctrl=0.454

#### crash300 detail
- T-C real n=5341, rand n=5341
- T-C non-cluster-start n=4571 (ctrl n=4571), cluster-start n=770
- T-A bucket counts: {'0': 367, '1': 1424, '2': 1678, '3+': 1872}
- T-B bucket counts: {'(0,0.46]': 57, '(0.46,1.38]': 4376, '<=0': 899, '>1.38': 9}
- T-D age bucket counts: {'0-2': 18, '3-8': 22, '9+': 8454}; losses=1232

### crash500
- spikes=3903 (cluster-start=544, solo=3359)
- T-C: discharge-following e+1 avg=0.126 (WR 0.74), rand avg=-0.026, diff=0.152
  - non-cluster-start entries avg=0.247 (n=3359) vs ctrl -0.052 -> diff 0.299
  - cluster-start entries avg=-0.624 (n=544)
- T-A: break-rate buckets (per-trade avg): {'0': 0.48873941368076734, '1': 0.24874437627811855, '2': 0.1002467476149144, '3+': -0.012933788395902947}; largest=3+ ctrl=-0.016
- T-B: bar1-fav buckets (per-trade avg): {'(0,0.18]': 0.022384615384593277, '(0.18,0.55]': 0.32332106927277804, '<=0': -1.5454343065693443} (med bar1 range=0.368)
- T-D: dense base avg=0.720 (n=11001), hard-mask 3-8 avg=0.801 (n=59), soft-skip 3+ avg=0.720 (n=10990), soft ctrl=0.270

#### crash500 detail
- T-C real n=3903, rand n=3903
- T-C non-cluster-start n=3359 (ctrl n=3359), cluster-start n=544
- T-A bucket counts: {'0': 307, '1': 978, '2': 1153, '3+': 1465}
- T-B bucket counts: {'(0,0.18]': 13, '(0.18,0.55]': 3479, '<=0': 411}
- T-D age bucket counts: {'0-2': 11, '3-8': 59, '9+': 10931}; losses=1008

### crash1000
- spikes=2208 (cluster-start=219, solo=1989)
- T-C: discharge-following e+1 avg=0.192 (WR 0.86), rand avg=-0.152, diff=0.344
  - non-cluster-start entries avg=0.353 (n=1989) vs ctrl -0.069 -> diff 0.422
  - cluster-start entries avg=-1.272 (n=219)
- T-A: break-rate buckets (per-trade avg): {'0': 0.6661991150442866, '1': 0.328335062240668, '2': 0.15056609642302676, '3+': 0.09621288659790968}; largest=3+ ctrl=0.069
- T-B: bar1-fav buckets (per-trade avg): {'(0,0.18]': 0.7630000000008295, '(0.18,0.53]': 0.4220523509452172, '<=0': -3.1095729166666595} (med bar1 range=0.351)
- T-D: dense base avg=0.766 (n=15554), hard-mask 3-8 avg=0.000 (n=0), soft-skip 3+ avg=0.766 (n=15554), soft ctrl=0.453

#### crash1000 detail
- T-C real n=2208, rand n=2208
- T-C non-cluster-start n=1989 (ctrl n=1989), cluster-start n=219
- T-A bucket counts: {'0': 113, '1': 482, '2': 643, '3+': 970}
- T-B bucket counts: {'(0,0.18]': 1, '(0.18,0.53]': 2063, '<=0': 144}
- T-D age bucket counts: {'0-2': 0, '3-8': 0, '9+': 15555}; losses=727

### boom500
- spikes=3810 (cluster-start=570, solo=3240)
- T-C: discharge-following e+1 avg=0.154 (WR 0.73), rand avg=0.042, diff=0.111
  - non-cluster-start entries avg=0.329 (n=3240) vs ctrl -0.106 -> diff 0.435
  - cluster-start entries avg=-0.842 (n=570)
- T-A: break-rate buckets (per-trade avg): {'0': 0.5959999999999368, '1': 0.26765384615378796, '2': 0.20911305070651182, '3+': -0.06619642857148625}; largest=3+ ctrl=-0.044
- T-B: bar1-fav buckets (per-trade avg): {'(0,0.28]': -0.5485294117648022, '(0.28,0.85]': 0.4870517549077307, '<=0': -2.4198190255220595} (med bar1 range=0.568)
- T-D: dense base avg=0.752 (n=8169), hard-mask 3-8 avg=0.000 (n=0), soft-skip 3+ avg=0.752 (n=8169), soft ctrl=0.023

#### boom500 detail
- T-C real n=3810, rand n=3810
- T-C non-cluster-start n=3240 (ctrl n=3240), cluster-start n=570
- T-A bucket counts: {'0': 286, '1': 949, '2': 1203, '3+': 1372}
- T-B bucket counts: {'(0,0.28]': 17, '(0.28,0.85]': 3362, '<=0': 431}
- T-D age bucket counts: {'0-2': 0, '3-8': 0, '9+': 8169}; losses=1179

### boom1000
- spikes=2151 (cluster-start=223, solo=1928)
- T-C: discharge-following e+1 avg=0.310 (WR 0.85), rand avg=-0.538, diff=0.849
  - non-cluster-start entries avg=0.705 (n=1928) vs ctrl -0.273 -> diff 0.978
  - cluster-start entries avg=-3.099 (n=223)
- T-A: break-rate buckets (per-trade avg): {'0': 1.128850515464477, '1': 1.0584326710822585, '2': 0.016792093704780027, '3+': 0.07298583878050056}; largest=3+ ctrl=-0.569
- T-B: bar1-fav buckets (per-trade avg): {'(0.45,1.35]': 0.8235663542186408, '<=0': -8.08008467741938} (med bar1 range=0.902)
- T-D: dense base avg=0.989 (n=11078), hard-mask 3-8 avg=-1.088 (n=26), soft-skip 3+ avg=0.993 (n=11058), soft ctrl=-0.469

#### boom1000 detail
- T-C real n=2151, rand n=2151
- T-C non-cluster-start n=1928 (ctrl n=1928), cluster-start n=223
- T-A bucket counts: {'0': 97, '1': 453, '2': 683, '3+': 918}
- T-B bucket counts: {'(0.45,1.35]': 2027, '<=0': 124}
- T-D age bucket counts: {'0-2': 20, '3-8': 26, '9+': 11033}; losses=836

## T-C distribution summary (cluster-start vs solo)
| sym | n_spikes | cluster-start | solo | cls_range_mean | solo_range_mean | cls_ratio_mean | solo_ratio_mean |
|---|---|---|---|---|---|---|---|
| crash300 | 5341 | 770 | 4571 | 6.78 | 6.72 | 7.32 | 7.22 |
| crash500 | 3903 | 544 | 3359 | 3.77 | 3.85 | 10.39 | 10.60 |
| crash1000 | 2208 | 219 | 1989 | 6.50 | 6.56 | 18.75 | 18.97 |
| boom500 | 3810 | 570 | 3240 | 5.81 | 5.87 | 10.42 | 10.50 |
| boom1000 | 2151 | 223 | 1928 | 17.46 | 17.00 | 19.70 | 19.14 |

---
