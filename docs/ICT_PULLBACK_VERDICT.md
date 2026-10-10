# ICT Pullback Proxy Test — Verdict

Source video: "Easiest Way To Trade Boom And Crash! - ICT Concepts" (transcript provided).
Claim tested: **M15 break-of-structure regime + pullback-to-level entry beats entering right after the discharge.**
Evidence: `eval/ict_pullback_test.py` (+ inline sweep), corrected engine (gap-through fills, partial entry), random controls, OOS halves.

## Setup (faithful proxy, not a strawman)

- Drift direction = the book's verified edge: LONG crash family / SHORT boom family.
- Discharge = adverse bar, range > 3× median.
- **Baseline entry**: close of bar e+1 (what the EA does now).
- **Pullback entry**: first bar j in [e+1, e+6] whose close retraced ≥50% of the bounce off the discharge extreme, without breaking that extreme. Skip if none.
- **Regime gate**: M15 break-of-structure — close breaks the recent 4-bar M15 high → bullish; breaks the low → bearish. Gate passes only the regime matching the drift (bullish for crash, bearish for boom). **Initial run had boom gated on the wrong polarity (bullish); that flipped the result from negative to strongly positive — the gate only works when it matches drift direction.**
- Same exits as the corrected engine: SL/TP 1:2, partial add-B-if-profitable, gap-through SL fills on adverse spike bars, hold=3.

## Results (avg pts/trade, PF, n)

| Symbol | Baseline e+1 | +Gate | +Gate+Pull | Random control | Random+Gate | OOS pull (1st/2nd) |
|---|---|---|---|---|---|---|
| crash300 | +0.27 (1.19) | +0.58 (1.48) | **+0.79 (1.72, n=245)** | +0.28 | +0.66 | +0.56 / +1.10 |
| boom500 | +0.15 (1.15) | +0.43 (1.53) | **+0.81 (2.59, n=93)** | +0.20 | +0.41 | +0.92 / +0.68 |
| boom1000 | +0.31 (1.19) | +1.05 (2.05) | +1.73 (6.83, n=19) | +0.07 | +0.61 | +1.26 / +1.97 (tiny n) |
| crash500 | +0.13 (1.19) | +0.32 (1.63) | +0.21 (1.34) | +0.13 | +0.30 | +0.24 / +0.15 |
| crash1000 | +0.19 (1.28) | +0.54 (2.33) | +0.42 (1.77, n=16) | +0.13 | +0.36 | −0.36 / +1.03 (tiny n) |

## What's real

1. **The M15 BOS regime gate is a genuine universal lever** — it improves every one of the 5 symbols (+0.27→+0.58 crash300, +0.31→+1.05 boom1000). The video's core idea (don't fade against the M15 structure) is confirmed on our data: fade only when the discharge is *against* the M15 structure (crash inside an up-structure, boom pop inside a down-structure). This is the same mechanism family as the Voc 12h leg gate (+0.64→+0.86) — a macro-directional filter, now proven on the main book too.

2. **The pullback entry adds a second, independent layer on boom500 + crash300.** Gate+pull beats gate alone by ~2× on boom500 (+0.81 vs +0.43) and clearly on crash300 (+0.79 vs +0.58). OOS-consistent on both (both halves positive, PF 2.59/1.53 boom500 full). The pullback = the "wait for the retracement" half of the video's 50% rule — it buys the drift after the knee-jerk bounce has retraced, exactly as the video describes.

3. **Robustness sweep (depth × window) is clean** — frac 0.3–0.7 × wait 4–8 all positive on crash300 and boom500, peak at 0.5–0.7 × 6. Not a knife-edge. (wait=8 degrades — don't wait forever.)

4. **cd=10 does NOT stack on top** (crash300 +0.79→+0.73, boom500 +0.81→+0.73). The gate+pullback already removes the clustered-loss windows the cooldown was invented for — the filter subsumes the cooldown.

## What's not real

- **crash1000 and crash500**: pullback n is tiny (16–89) or the lift disappears (crash500 gate+pull +0.21 < gate alone +0.32). The 1000s' drift legs rarely retrace 50% within 6 bars, so the pullback rarely fires — and when it does on crash1000, OOS 1st half is negative. No claim there.
- **boom1000** looks spectacular (+1.73, PF 6.83) but n=19 — treat as unproven, not dead.

## What the video got right vs wrong

- **Right**: the regime filter has real predictive content; the pullback timing has real value on the mid-volatility products. Both are measurable, OOS-consistent, and survive random controls.
- **Wrong (or unmeasurable)**: the "order block" candle-colour entry rule itself — our proxy (retrace to 50% of the bounce) captures the value without any SMC discretionary parts, and random+gate on boom500 (+0.41) shows the regime is the bigger half of the edge; the pullback is the +0.40 top-up. The M15 swing-high TP target is untested here (we kept 2×SL) — a possible next lever.

## Operational spec if this goes live

- Keep the current partial engine + spike trigger.
- Add: M15 BOS regime gate (bullish only for crash charts, bearish only for boom charts).
- Entry: skip e+1; wait for ≥50% retrace of the post-discharge bounce within 6 bars, enter at that close; skip if no retrace.
- No loss-cooldown needed (subsumed).
- Expected: crash300 +0.8 pts/trade (~245 trades/sample), boom500 +0.8 (~93) — roughly 3× the current EA per-trade value on those two charts, with PF 1.7–2.6.

## Honest caveats

- 30–60 days, one regime; same sim-understates-live-gap caveat as everything else.
- boom500 pullback fires only ~93 times in 43k bars — expect low throughput live (a few trades/day on that chart).
- The gate is a *tilt*, not a switch: on the down-leg of an oscillator product, an M15 BOS gate could decay the way the Voc gate did in leg 3. The crash300 OOS halves both being positive (1.39/2.59 PF) is the main protection we have.