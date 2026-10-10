# Freshness × Dense Schedule — Verdict

Question: does the BOS-freshness edge (`REGIME_FAMILY_BATTERY_VERDICT.md`) transfer
to the dense schedule mode (`SCHEDULE_OPT_VERDICT.md` — every-bar entries, cd=10
after losses, hold=2 partial)?
Evidence: masked `run_schedule` (verified engine), 5 symbols, OOS halves.

## Answer: NO — freshness is a pullback-entry property, not a time-of-regime property

Dense schedule, step=1, cd=10, hold=2 (PnL per 1,000 bars = throughput):

| Symbol | base | + M15 gate | + gate + freshness 3-8 |
|---|---|---|---|
| crash300 | +0.74 (PF 2.02, 289/1kb) | **+0.92 (PF 2.54, 188/1kb)** | +0.72 (PF 1.97, 36/1kb) |
| crash500 | +0.32 (2.02) | **+0.40 (2.60)** | +0.29 (1.82) |
| crash1000 | +0.34 (2.09) | **+0.44 (2.91)** | +0.32 (1.94) |
| boom500 | +0.52 (2.10) | **+0.62 (2.59)** | +0.50 (2.03) |
| boom1000 | +0.88 (2.08) | **+1.04 (2.55)** | +0.93 (2.25) |

Adding freshness to the dense schedule **drops EV back to (or below) base** on every
symbol and cuts throughput ~85%. It doesn't transfer.

Why: the freshness edge was measured on **pullback entries** — trades that only fire
3-8 bars after a BOS because the *entry rule* waits for a retrace. The dense schedule
enters continuously; masking it to the age-3-8 window just deletes most of the drift
windows while keeping none of the pullback logic. The "structure needs 45-90 min to
settle" result is about *when a retrace entry works*, not about the drift being weak
elsewhere — dense mode already harvests all of it.

## The finding the question surfaced: the M15 gate upgrades dense mode universally

The gate alone (no freshness) on the dense schedule:

- **EV up 15-30% and PF up ~0.5 on all 5 symbols** (+0.74→+0.92 crash300, +0.34→+0.44
  crash1000, +0.88→+1.04 boom1000).
- **OOS-consistent both halves on all 5**: crash300 +1.06/+0.78, crash500 +0.41/+0.39,
  crash1000 +0.43/+0.45, boom500 +0.63/+0.60, boom1000 +1.04/+1.03.
- Cost: throughput −35% (fewer gated bars), but PF 2.5-2.9 is the best dense profile
  measured anywhere in this project.

The same direction-vs-drift logic that made the gate work for pullback entries works
for every-bar entries: don't fade into a structure that's breaking against you.

## Bottom line

- **Dense mode spec updated**: every-bar entries + cd=10 + **M15 BOS gate**. Keep
  freshness OUT of dense mode.
- **Freshness stays a pullback-mode-only layer** (crash300/boom500 spike-triggered
  config, per `REGIME_FAMILY_BATTERY_VERDICT.md`).
- The two modes now have clean, separate specs: dense = throughput engine (gated,
  ~160-400 PnL/1kb), pullback = precision engine (~+1.1/trade on its charts).