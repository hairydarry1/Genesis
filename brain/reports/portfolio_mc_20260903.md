# Portfolio Monte Carlo - 20260903

30-day months, 200,000 months for distributions, 40,000 paths for DD. Days bootstrap-drawn per symbol, Gaussian-copula coupled at the measured (shrunk) correlation (near independent).
Kelly-corrected risk: crash300 2%, crash500 2%, crash1000 1.5%, boom500 2%, boom1000 1%. OLD = 2% everywhere. $ frame: 5 lots x $1/pt/lot (live-measured).
m = uniform multiplier on loss-side daily draws: 1.0 = sim tails (optimistic), 1.5-2.0 = realistic live gap friction (live SL hits measured 4-9x SL vs ~2x in the bar sim), 2.5 = severe.

Portfolio EV breakeven loss multiplier: 1.60 (below 1.0 means the book is negative even in the sim).

## 5-lot book at m=1.0
med  +9644.30$  mean  +9644.66$  sd  +992.22$  p1  +7333.85$  min  +5382.30$  P<0   0.0%
intra-month max DD $ p50/p90/p95: 35/161/202, worst 531

## 5-lot book at m=1.5
med  +1676.86$  mean  +1672.87$  sd +1363.46$  p1  -1519.43$  min  -4338.99$  P<0  11.0%
intra-month max DD $ p50/p90/p95: 813/1508/1783, worst 4658

## 5-lot book at m=2.0
med  -6288.45$  mean  -6298.92$  sd +1755.99$  p1 -10436.21$  min -14149.06$  P<0 100.0%
intra-month max DD $ p50/p90/p95: 6232/8441/9047, worst 14068

## 5-lot book at m=2.5
med -14253.37$  mean -14270.71$  sd +2158.23$  p1 -19369.77$  min -23959.13$  P<0 100.0%
intra-month max DD $ p50/p90/p95: 13802/16587/17341, worst 23478
