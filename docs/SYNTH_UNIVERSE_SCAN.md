# Deriv Synthetic Universe Scan: Is There a Better Vehicle Than flip1000? (Sep 10, 2026)

**Question:** scan all quoted Deriv synthetics for phase-persistence edges with tradeable
spreads — is there anything better than the flip1000 config for the $10→$100 mission?

**Verdict up front: no.** The full funnel (170 symbols → 150 quoted → 34 with bar-level
persistence structure → 12 weekly-stable over 30 days → 11 backtested net of live spread)
produced **zero vehicles that clear friction**. flip1000 @5.0 min_age=1 remains the only
config in this repo where measured structure survived honest spread costs — and the scan
explains *why*: the Flip indices' spike mechanism creates intrabar jumps a phase-follower
can capture, while every other synthetic's persistence is bar-scale grind that doesn't
clear the toll.

## Tooling built

| File | Role |
|---|---|
| `analysis/deriv_scan.py` | Universe discovery + **live** spread measurement (the Flip-study lesson applied at step one) |
| `analysis/synth_bar_scan.py` | Stage-1 M1-bar structure scan, all 150 quoted symbols, cached bars in `data/synth_scan/` |
| `analysis/synth_deep_dive.py` | Stage-2 weekly stability, 30d of M1 per shortlisted symbol |
| `analysis/synth_edge_backtest.py` | Stage-3 phase-follower backtest, live-spread dollars, min-lot economics + flip1000 control |
| `analysis/synth_one_sided.py` | One-sided follower splits + buy&hold checks |

## Key infrastructure finding: no local tick history for un-streamed symbols

The first attempt (tick-level scan of all 170 symbols) failed structurally: the terminal
holds no tick archive for symbols it hasn't streamed — each `copy_ticks_range` is a ~100 s
server roundtrip that usually returns **empty**. M1 bars (`copy_rates_range`) return in
~0.5 s/symbol and top out at **30 days** of history. Hence the funnel: bars for screening,
ticks (via the running daily collector + a live recorder) only for finalists.

## Stage results

**Stage 1 — structure (150 symbols, 72h M1):** 34 symbols show large-move same-direction
persistence (same@3 > 0.5) with toll_bar < 0.5. Zero mean-reverters (VR(5) < 0.85) anywhere.
Boom/Crash family reads same@3 = 1.000 (known spike mechanics, known dead under friction).

**Stage 2 — weekly stability (30d, 5 weeks):**

| Symbol | mean same@3 | min week | all > 0.5 | $/RT @0.1 lot |
|---|---|---|---|---|
| Trek Down | 0.999 | 0.997 | YES | $0.0072 |
| Trek Up | 0.999 | 0.996 | YES | $0.0104 |
| Vol over Crash 400 | 0.905 | 0.878 | YES | $0.0111 |
| Vol over Crash 550 | 0.724 | 0.702 | YES | $0.0051 |
| Skew Step 5 Up/Down | 0.66 | 0.58 | YES | $0.08–0.09 |
| Range Break 100 | 0.575 | 0.518 | YES | $0.26 |
| Range Break 200 | 0.530 | 0.467 | no | $0.20 |
| Volatility 30/100 (1s) | 0.515 | 0.48 | **no** | $0.0006–0.0038 |

The (1s) Volatility family's 0.515-average is noise: bad weeks dip to ~0.48. Skew Step
4 Up/Down pass stability but at 0.52–0.54 — barely above coin.

**Stage 3 — net of live spread (30d, min-lot $ economics):**

| Symbol | best config | net/wk | maxDD |
|---|---|---|---|
| Range Break 100 | m=3 follower | +$24.44 (at 0.1-lot $/unit) | **$266** |
| Trek Down | m=2 | +$1.74 | $62 |
| everyone else | — | **negative** | — |
| **flip1000 control (bars)** | m=2 | +$14.15 | $30 |

## The control that explains everything

flip1000's own M1 bars through the identical engine: **+$8.9–14.2/wk vs the +$27/wk
tick-level truth.** Bar aggregation destroys ~⅔ of the spike-phase PnL — spikes are
intrabar events; the bar follower detects them one bar late and pays the spread across a
bigger gap. Two consequences:

1. Bar-level backtest numbers here are **lower bounds** for spike-driven symbols but
   near-truth for grind-driven ones.
2. Even granting that optimism, every candidate still lost money — their persistence is
   grind-scale, and grind doesn't clear the spread. The Flip indices are structurally
   special *because* their edge lives in spikes.

## Loose ends resolved

- **Trek "1.000 persistence" is a mechanical artifact:** 30 days, 2,460 up-moves vs ONE
  down-move (Trek Up) — by construction there are no counter-moves to trade. Buy&hold:
  −$6.65 (Up) / +$0.33 (Down) per 30d. No ownable drift.
- **Range Break 100 asymmetry is real but small:** long-follower +$17.0 vs short-follower
  −$39.9 per 30d — its boundary mechanics favor long break-phase persistence. But at the
  0.01 min lot this is ~$4/wk with a $266-style DD profile. Not a $10 vehicle; noted for
  the archive.

## Standing conclusion for the mission

The search space is now measured, not assumed: **no better vehicle exists among the quoted
synthetics.** flip1000 @5.0 min_age=1, fixed 0.1 lot, daily stop −$9 keeps its crown, and
the two things that decide it remain exactly the two instruments already running: the
daily tick collector (edge persistence, resolves ~mid-October) and the demo executor's
slippage ledger (true execution cost, accumulates now).

*Caveats: bar-level stage-1/2 metrics understate spike-driven persistence (calibration
above); spreads move and were sampled at scan time; 30 days is the terminal's full bar
history for these symbols.*
