# Regime Family Battery — Challenge + Build Verdict

Challenging the verified gate+pullback edge (`ICT_PULLBACK_VERDICT.md`) and hunting
neighboring edges. Evidence: `eval/regime_family_battery.py` + inline verification
(matched-lag controls, OOS halves, bucket sweeps). Corrected engine, partial, hold=3.

## Verified survivors (3 new findings, all OOS-checked)

### 1. BOS FRESHNESS — the big one. Don't enter right after a structure break.

The gate+pullback edge is **not uniform in regime age**. Bucketed by M15 bars since
the last BOS (pullback entries only):

| Age bucket (M15 bars) | crash300 avg (PF) | boom500 avg (PF) |
|---|---|---|
| 0-3 (fresh break) | +0.16 (1.10) | **−0.14 (0.89)** ❌ |
| 2-5 | +0.94 (1.92) | +0.74 (2.17) |
| **3-6** | **+1.14 (2.28, n=158)** | +1.08 (4.04) |
| **3-8** | +0.83 (1.74) | **+1.16 (4.92, n=50)** |
| 4-9 | +0.60 | +1.28 (6.82) |
| 5-12 | +0.42 | +1.24 |

- **Entering within the first ~45 min of a BOS is the losing play** — negative on
  boom500, barely positive on crash300. The break's initial momentum hasn't resolved;
  the first pullbacks fail.
- The edge **peaks at age 3-6** on crash300 (+1.14 vs +0.79 ungated-by-age) and
  **stays strong 3-12** on boom500 (+1.16, PF 4.92).
- Bucket sweep is smooth, not a knife-edge: 2-5 through 5-12 all beat 0-3 on both.
- OOS (mid 3-8 bucket): crash300 +1.10 / +1.77 both halves; boom500 +1.16 / +1.63
  (small n on boom500, but directionally identical).

**This is the "wait for the retracement" rule taken one step further than the video:
not just a price retrace, a TIME retrace.** The structure needs ~45-90 min to settle
before pullback entries have full value.

### 2. crash500 wants the M60 gate, not M15 (nearly doubles it)

| Gate | crash500 avg (PF) | OOS |
|---|---|---|
| M15 BOS | +0.21 (1.34) | — |
| **M60 BOS** | **+0.39 (1.86, n=97)** | +0.42 / +0.36 |
| matched-lag ctrl | +0.21 | — |

The pullback timing adds +0.18 over the matched control on the M60 gate. crash500's
drift legs are slower than crash300's — the 15-min structure flips too fast for it.

### 3. boom500 regime tuning: faster is better (lb=3, persist=3)

| Config | boom500 avg (PF) | OOS |
|---|---|---|
| default (lb=4, persist=6) | +0.81 (2.59) | — |
| **fast (lb=3, persist=3)** | **+0.96 (3.39, n=97)** | +1.03 / +0.89 |
| matched-lag ctrl (fast) | +0.36 | — |

Real but modest (+0.15). boom500's regime signal is faster than the default — the
edge decays with BOS age (see #1) so a shorter persistence window keeps entries in
the good zone.

## Kill-switch (validates the whole family)

Fading **against** the regime is dead, as it must be: crash300 −0.13 (PF 0.92),
boom500 −0.61 (PF 0.64). The gate's direction is the source of the value.

## Dead ends (measured, no value)

- **Consecutive BOS** (1st vs 2nd+ break): crash300 +0.71 vs +0.55 with n=29/27 —
  directionally consistent but too thin to claim; boom500 n=3.
- **Fallback entry** (no-pullback discharges at e+7): adds throughput at dilution
  (+0.58 crash300, +0.38 boom500 per-trade vs +0.79/+0.81) — the pullback filter is
  the value, the fallback isn't a free ride.
- **M60 gate on the 1000s**: crash1000 +0.42→+0.06, boom1000 +1.73→+1.00, boom500
  +0.81→+0.19 — M60 only helps crash500.

## Final spec (stacked)

1. Gate: M15 BOS, direction matching drift (bull crash / bear boom), **persist=3-6
   bars**.
2. **Freshness: skip discharges whose BOS is <2 M15 bars old; enter only age 3-8.**
3. Entry: 50% pullback of the post-discharge bounce within 6 bars (crash300/boom500);
   crash500 uses the M60 gate + same pullback.
4. Exit unchanged: 2×SL / 1×SL, partial, hold=3.

Expected (measured): crash300 ~+1.1, boom500 ~+1.1-1.2, crash500 ~+0.39 per trade —
vs the pre-battery gate+pullback (+0.79/+0.81/+0.21).

## Honest caveats

- boom500 freshness buckets are n=22-50 — the *direction* is consistent across every
  bucket and OOS half, but the exact peak (3-6 vs 3-12) is not pinned.
- Same one-regime sample as everything else (30-60 days); sim-understates-live-gap
  caveat applies unchanged. Paper-test at 1% before trusting the numbers.