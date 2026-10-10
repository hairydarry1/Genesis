# Tail Risk: Why Per-Trade Stops Don't Work Here (Sep 9, 2026)

**Question:** "What about the fact that stop-losses don't work?"
**Short answer:** Correct — on Boom/Crash spike-fades a per-trade stop is
either noise (blown through, filled at the spike price) or an edge-killer
(mid-spike market exit). Three independent measurements below. The
consequence is the Phase 3 risk model: **size to the tail, never stop into
it.**

## 1. The spike path makes any practical stop meaningless

Boom/Crash spike bars run **9–55× the median M1 range** (Sep 1–7, spikes
>3× median only):

| Symbol | n spikes | p50 | p95 | p99 | MAX |
|---|---|---|---|---|---|
| boom500 | 945 | 9.2× | 20.9× | 25.6× | 37.0× |
| crash500 | 892 | 9.6× | 21.4× | 28.7× | 38.6× |
| boom1000 | 497 | 17.9× | 40.8× | 52.5× | 60.7× |
| crash1000 | 502 | 17.9× | 42.2× | 55.0× | 72.9× |

Measured repeatedly in this project (EA audit E-ladder, original diagnosis):

- Stops inside the spike path get **blown through**: fills happen at the
  spike tick, not the stop level. EA variants A/B (1.5×/1.0× stops) produced
  *identical* results — every stop filled at the spike price. Realized
  "stopped" losses were 10–15× the intended risk.
- A stop far enough out to survive a p99 spike (50× median ≈ 30% of price)
  risks ~30% of the account per trade — the strategy dies of tail
  accumulation even at 57–70% WR.
- Exiting *mid-spike* with a market order is the worst trade of the week
  (that is what the tick-level safety exit does: −$931/wk measured). Price
  mean-reverts after spikes; the bar-close `spike_against` exit prices
  nearly identically **without** paying the spike tick.

## 2. What the tail actually costs (validated fade, Sep 1–7, fixed sizing)

All worst losses are `spike_against` exits — **multi-bar spike sequences**
(1–4 bars, adverse moves 15–39% of price), not single ticks. Forensics:
boom500 worst −24.9% move in 1 bar (hour 1); crash500 −39.1% in 2 bars
(hour 19); boom1000 −29.3% in 4 bars (hours 14–15); crash1000 −23.4% in 1
bar (hour 19). These cluster in cold hours 14–15 and 19 — the hours the
edge trades most.

| Sizing basis (risk distance) | Portfolio/wk | Worst single trade | Worst as % of $200 |
|---|---|---|---|
| 1.5× median (current "2% nominal") | **+$596.00** | −$132.20 | **−66%** |
| p95 spike (boom500 20.9× … crash1000 42.2×) | +$52.87 | −$6.08 | −3.0% |
| p99 spike | +$41.29 | −$4.52 | −2.3% |
| MAX spike seen | +$30.61 | −$3.37 | −1.7% |

Per-symbol at 1.5× basis: boom500 +$390.80 (worst −27.8%), crash500
+$494.23 (worst −43.3%), boom1000 +$364.38 (worst −66.1%), crash1000
−$653.42 (worst −52.6%).

**Interpretation:** the +$596/week edge is real but carries unbounded
per-trade risk. One p99+ spike sequence = 25–66% of the book. The sim lets
losses exceed equity (crash500 −$499 on a $200 book); live, that is a
margin stop-out. The "2% risk" label is fiction: 2% is the risk of a
*1.5×-median adverse move*, which spikes exceed by 6–36×.

## 3. The rule: size to the tail, never stop into it

`units = risk_usd / (basis × median_range)` — the ONLY free parameter is
the basis, and it must be a **multiple of the realistic tail**, not the
median:

- **p99 basis** (recommended): worst observed trade ≤ 2.3% of book;
  portfolio +$41.29/wk (~+20%/mo on $200) with survivable tails. Not
  self-deceiving: p99 WILL be exceeded eventually (~1 trade in 100), so
  the true tail is the MAX basis or a hard account-level catastrophe stop.
- **MAX basis** (seen): +$30.61/wk, worst −1.7%. Even this is not a
  guarantee — a spike beyond Sep's max (72.9×) is possible; Boom/Crash
  spikes are structurally unbounded (the 1000-series drifted 17.9×→
  72.9× within one week).
- **Per-trade SL: drop it from the design entirely** (it already is, in
  the validated fade). Protection lives at the ACCOUNT level instead:
  (a) tail-based sizing as above, (b) hard equity floor that halts
  everything (e.g. −25% of account), (c) no compounding until the tail
  model has a multi-month track record (compounding amplifies exactly
  when the tail lands — the −$174 vs +$596 sizing-path flip).

**Portfolio note:** crash1000 is negative at *every* sizing basis
(−$653 → −$23 → −$18 → −$13/wk). Its inclusion was justified for the
measurement phase; for Phase 3 sizing, symbols are sized independently
(per-symbol books) so a negative-edge symbol cannot tax the others, and
the Sep 23–24 review's pre-registered suspension criterion applies.

## 4. Win-units economics — the asymmetry is the strategy (Sep 9 addendum)

Expressing every trade in **units of the average win** (fixed sizing, Sep 1–7)
makes the fragility explicit. Breakeven WR = |avgLoss| / (avgWin + |avgLoss|):

| Symbol | avg loss (win-units) | breakeven WR | actual WR | margin | tails (≥3 wins) |
|---|---|---|---|---|---|
| boom500 | 0.94 | 48.5% | 55.6% | **+7.1pp** | 0 trades (worst 3.0 wins) |
| crash500 | 1.09 | 52.1% | 60.3% | **+8.2pp** | 1 trade (4.5 wins) |
| boom1000 | 2.07 | 67.4% | 73.2% | **+5.8pp** | 7 trades, 28 win-units erased |
| crash1000 | 2.32 | 69.9% | 59.8% | **−10.1pp** | 11 trades, 44 win-units erased |

Portfolio week: **301 win-units earned vs 268 erased — the entire edge is a
12% surplus on gross win flow** (ex-crash1000: 1.35). Consequences:

- The margin over breakeven is only **6–8pp of WR**. A 5pp WR drift (which
  crash1000 actually did Aug→Sep) moves a symbol from best to negative.
- On the 1000-series the average loss already costs ~2 wins and tails of
  3–6.3 wins hit in 7–12% of trades — one week of boom1000 trading earned
  71 wins and handed back 54 of them.
- WR flip test (boom500): at WR 50% EV is +$0.55/trade; at 48% it is −$0.18.
  The strategy has no cushion below ~50% realized WR.
- This is why Phase 2's gates matter: the pre-registered suspension rule
  (PnL<0 AND WR<55%) is exactly a breakeven-margin test, and why the halt
  cage was lethal — halting after loss clusters removes the win flow that
  refills the 12% surplus while leaving the tails in place.

## 5. Standing implications

1. **Phase 2 measurement (running):** unaffected — paper only. But the
   realized-loss distribution it collects is exactly the data needed to
   refine the tail model (p95/p99/max per symbol, per month).
2. **EA alignment (docs/EA_PHASE1_ALIGNMENT_AUDIT.md):** the aligned EA
   must NOT attach an SL (already in punch-list item 2) and must size on
   the tail basis, not the 1.5×-median basis, before any real-money use.
3. **The halt-cage findings** (docs/REPLAY_VS_BACKTEST_GAP.md) compound
   this: halts tuned to the *nominal* 2% risk are nonsense when real
   losses run 10–40× nominal. Any Phase 3 halt must be expressed in
   tail-realized dollars, not nominal-risk multiples.

## 6. Reproduce

```bash
python eval/diagnose_replay_gap.py            # E-ladder (EA audit) + variant ladder
# fixed-sizing tail ladder + worst-loss forensics:
python - <<'PY'
import sys; sys.path.insert(0, '.')
from eval.diagnose_replay_gap import run_variant, load_ticks, load_candles
cfg = {'timeout_bars': 8, 'tick_tp': False, 'use_sl': False, 'sl_mult': 0,
       'tp_mult': 0, 'use_safety': False, 'use_halts': False,
       'cold_hours': 'P1', 'cooldown_bars': 0}
for sym in ['boom500','crash500','boom1000','crash1000']:
    df = load_candles(sym)
    rng = df['high']-df['low']; med = rng.rolling(500, min_periods=50).median()
    spikes = (rng/med).dropna(); spikes = spikes[spikes>3.0]
    ts, prices = load_ticks(sym)
    eng = run_variant(sym, ts, prices, cfg, fixed_sizing=True)
    print(sym, eng.trades and len(eng.trades))
PY
```

*(The `run_variant(..., fixed_sizing=True)` switch was added for this
analysis; trade records now carry entry/exit prices, timestamps, units,
and mark-to-market equity for forensics. Diagnostic imports silence the
tracker writer at module load, so ad-hoc runs can never pollute the live
paper log again — a leak that occurred and was surgically repaired on
Sep 9.)*
