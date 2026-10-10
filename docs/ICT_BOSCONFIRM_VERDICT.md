# ICT M1-BOS-Confirmation Test — Corrected Verdict

Source: Gemini's visual audit of the video, Example 4 — when price never reaches the
50% level after a discharge, the author waits for an **M1 micro break-of-structure**
and enters on the confirmation bar instead of abandoning the trade.
Evidence: `eval/ict_bosconfirm_test.py` (raw comparison) and
`eval/ict_bosconfirm_proper.py` (matched-lag control + bootstrap + time-thirds).

## Corrected conclusion: NOT a real edge — the earlier crash500 claim was overstated

The first test compared BOS-confirm against an UNMATCHED control (random bars gated
at the random bar, not at discharges). Tested properly — same gated discharge set,
entry lags drawn from the empirical BOS-lag distribution — the timing rule adds
**nothing statistically distinguishable** on any symbol:

| Symbol | BOS-confirm avg | Matched-lag ctrl avg | diff | perm-p | 95% CI |
|---|---|---|---|---|---|
| crash500 | +0.339 (PF 1.74) | +0.222 (PF 1.38) | +0.117 | 0.18 | (−0.10, +0.32) |
| crash300 | +0.585 (PF 1.51) | +0.443 (PF 1.35) | +0.142 | 0.20 | (−0.19, +0.46) |
| boom500 | +0.470 (PF 1.57) | +0.346 (PF 1.39) | +0.124 | 0.24 | (−0.27, +0.48) |

- **All three permutation p-values ≈ 0.18–0.24** — far from significance.
- **All three 95% CIs cross zero** — the difference is consistent with noise.
- The positive averages are the **gate + drift edge** (present in the matched control
  too, and in all time-thirds), not the confirmation-bar timing.

## What remains true

1. **The M15 BOS gate + 50% pullback edge is real** (established separately with
   matched controls and OOS in `ICT_PULLBACK_VERDICT.md`) — crash300 +0.79 (PF 1.72),
   boom500 +0.81 (PF 2.59), OOS-consistent both halves, robustness-swept.
2. Time-thirds of BOS-confirm are all positive on every symbol — but that's the
   underlying drift edge, not the timing rule.
3. **TP-at-prior-swing is dead** (`ICT_SWINGTP_VERDICT.md`) — both M15 and M1 readings.

## Bottom line

Gemini's Ex-4 improvisation — like the order-block candle rule itself — is **visual
decoration with no measurable value**. The video's real content is the regime gate
(confirmed real) and the pullback timing (confirmed real on crash300/boom500). Every
discretionary "adjustment" the presenter makes to handle setup failures measures as
noise. The previous note that "crash500 should use BOS-confirm entries" is **retracted**:
that margin (p=0.18, CI crossing zero) is not a basis for a config change.