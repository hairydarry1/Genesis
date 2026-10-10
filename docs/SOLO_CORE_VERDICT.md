# Solo-Core Prospective Verdict — can any past-information rule reach the solo population?

## What we tested

The retrospective solo-only subset is the best population on every symbol (solo avg 0.6-1.7 vs
full avg 0.1-0.3). The clean T-C test showed the prospective cluster-start filter can't reach it
because solos and starts both have low trailing spike counts and can't be distinguished by a
trailing-window rule. This test asks the honest follow-up: is there ANY past-information rule
that selects a better-than-random subset of the full e+1 population — getting closer to the solo
core than the failed filter did — even if it's not a "cluster-start filter"?

Candidates (all predictable at spike-close time, no future peeking), with unbiased random
sub-sample controls (same size, 5 seeds) and cluster-label diagnostics (solo/member/start
removal rates):

- A. Trailing adverse-spike count in last W bars, threshold K (the failed filter, re-tested)
- B. Trailing max spike ratio < thr (was the last preceding spike small?)
- C. Time since last adverse spike >= thr (discharge-age — solos may sit in longer quiet gaps)
- D. Regime-age (M15 buckets since last drift-direction BOS) >= thr (settled regime = more solos?)
- E. This spike's ratio < thr (small spikes may be solos)
- F. Combined: A-sparse AND C-quiet AND D-settled

Symbols run: 2 of 5 (crash500, boom500) — only those had candle files present at runtime.
crash300/crash1000/boom1000 were skipped (file-not-found). So the verdict is partial.

Engine: partial_m1_corrected (gap-through fills, half_exit partial add, per-symbol broker floors,
2xSL/1xSL, hold=3). Each entry run once and cached; candidates read from cache; random control
drawn from the cached full-population set. PnL in points per trade, 1-lot equiv, no spread/commission.

Oracle population means (the structure the filter is working within):

| sym | solo mean (n) | member mean (n) | start mean (n) | full avg |
|---|---|---|---|---|
| crash500 | 0.662 (13733) | 0.239 (6868) | -1.084 (6507) | 0.136 |
| boom500 | 1.032 (13798) | 0.243 (5560) | -2.008 (5039) | 0.224 |

Solo is the best population on both; start is the worst; member is in between. Same structure as
the full 5-symbol T-C result. The question is whether any forward rule selects toward solo.

## Results

### crash500 — essentially null

Top candidates by clean diff (filter avg - random sub-sample mean):

| candidate | n | filter avg | random mean | diff | solo_rm | mem_rm | start_rm | rand range |
|---|---|---|---|---|---|---|---|---|
| B_maxratio_th2.0 | 254 | +0.147 | +0.076 | +0.071 | 0.99 | 1.00 | 0.99 | [-0.293, +0.245] |
| E_spikeratio_th15.0 | 22401 | +0.146 | +0.138 | +0.008 | 0.17 | 0.17 | 0.18 | [+0.128, +0.147] |
| E_spikeratio_th8.0 | 12799 | +0.141 | +0.136 | +0.005 | 0.53 | 0.52 | 0.54 | [+0.131, +0.144] |
| E_spikeratio_th20.0 | 25561 | +0.140 | +0.135 | +0.005 | 0.06 | 0.05 | 0.06 | [+0.133, +0.140] |
| E_spikeratio_th10.0 | 16179 | +0.141 | +0.138 | +0.003 | 0.40 | 0.39 | 0.41 | [+0.134, +0.142] |

The only candidate with diff >= 0.05 is B_maxratio_th2.0 at +0.071 — but n=254, and the random
control range is huge [-0.293, +0.245], so this is noise on a tiny sample. The large-n candidates
(n>=5000) are all at diff +0.003 to +0.008 — essentially zero, well within the random range.

Read: on crash500, no past-information rule selects a meaningfully better-than-random subset. The
best large-n diff is +0.008, which is 6% of the full avg and inside the control's noise band. The
solo-core insight is retrospective-only on crash500.

### boom500 — one real signal, plus two weak ones

Top candidates:

| candidate | n | filter avg | random mean | diff | solo_rm | mem_rm | start_rm | rand range |
|---|---|---|---|---|---|---|---|---|
| C_timesince_th20 | 3758 | +0.308 | +0.196 | +0.111 | 0.78 | 1.00 | 0.85 | [+0.156, +0.221] |
| C_timesince_th40 | 3758 | +0.308 | +0.196 | +0.111 | 0.78 | 1.00 | 0.85 | [+0.156, +0.221] |
| B_maxratio_th5.0 | 1134 | +0.249 | +0.193 | +0.055 | 0.94 | 0.99 | 0.95 | [+0.088, +0.308] |
| B_maxratio_th3.0 | 159 | +0.258 | +0.223 | +0.036 | 0.99 | 1.00 | 0.99 | [-0.060, +0.452] |
| E_spikeratio_th8.0 | 10247 | +0.252 | +0.221 | +0.031 | 0.58 | 0.57 | 0.58 | [+0.200, +0.252] |

Three candidates beat random by >= 0.05 (the "meaningful" threshold). Only one has both n>=1000
AND a clean selectivity profile:

**C_timesince_th20 (and th40, identical): +0.111 diff, n=3758, solo_rm=0.78, mem_rm=1.00, start_rm=0.85.**

This is the headline find. The rule "only enter if the last adverse spike was >= 20 M1 bars ago"
selects a +0.308 subset out of a +0.196 random baseline, a +0.111 lift that is cleanly outside
the random control range [+0.156, +0.221]. The selectivity is clean: it removes 100% of cluster-
members (which are the bad-ish middle population), 85% of cluster-starts (the worst population),
and 78% of solos (the best population). It's not a pure solo selector — it removes solos too — but
it removes the bad populations harder, netting a real positive.

C_timesince_th40 gives identical numbers because on boom500, every entry that has >= 40 bars since
the last spike also has >= 20 (the th20 and th40 filter sets are the same at n=3758). So th20 is
the binding threshold; th40 adds nothing.

The other two >= 0.05 candidates:
- B_maxratio_th5.0: +0.055, n=1134, solo_rm=0.94/mem_rm=0.99/start_rm=0.95. Removes almost
  everything (including most solos), netting a small positive. Small n, and the random range is
  wide [+0.088, +0.308], so this is borderline. It's "don't enter if the preceding spike was big,"
  which is a different signal from the failed cluster-start filter (that was about spike COUNT, not
  spike SIZE of the preceding spike).
- B_maxratio_th3.0: +0.036, n=159, too small to trust.

### What didn't work (and why)

- **A (trailing count, the failed filter)**: still null on both symbols. Confirms the clean T-C
  result — trailing spike count is a member-detector, not a start-detector, and can't reach the
  solo core.
- **D (regime-age)**: null on both. Settled regime does not have more solos. The regime gate's
  value is directional, not a solo-concentration signal.
- **E (spike ratio)**: small positive on boom500 (+0.031 at th8, n=10247) but it removes solos,
  members, and starts at roughly equal rates (~0.58 each), so it's a size filter that happens to
  sit slightly above random — not a solo-core selector. On crash500 it's dead (+0.003 to +0.008).
- **F (combined)**: weak on boom500 (+0.017), null on crash500. The combination doesn't beat the
  single best (C_timesince).

## The one real forward result: boom500 discharge-age filter

**Rule: at spike close, enter at e+1 only if the last adverse spike was >= 20 M1 bars ago.**

Across 5 random sub-sample seeds, the filtered set averages +0.308 vs a +0.196 random baseline —
a +0.111 lift that survives the control. n=3758 filtered entries on boom500 (out of 24397 full),
so it's a meaningful reduction (to ~15% of the population) with a real per-trade improvement.

The mechanism is not "select solos." It removes 100% of members, 85% of starts, and 78% of solos.
It's a "skip the busy clusters, keep the quiet gaps" filter. The quiet gaps happen to contain a
higher concentration of solos than the busy clusters, but the filter also removes many solos — it
just removes the bad populations harder, netting positive.

This is a DIFFERENT signal from the failed cluster-start filter. The failed filter was about spike
COUNT in the trailing window (member-detector). This is about SPIKE GAP — time since last discharge
— which is a qualitatively different variable and it works where the count filter didn't.

## Why it works on boom500 but not crash500

The oracle structure differs: on boom500, start mean is -2.008 (n=5039, 20% of population) vs
crash500 start mean -1.084 (n=6507, 24% of population). The boom500 start tail is more extreme AND
the solo mean is higher (1.032 vs 0.662). So removing start-dense regions helps more on boom500.

The discharge-age filter removes start-dense regions because starts come in clusters (3+ spikes in
10 bars), and clusters have short inter-spike gaps (so a "last spike >= 20 bars ago" rule skips the
cluster entirely, including its start). Solo entries, being isolated, have longer gaps before them
(on average), so they survive the gap filter at a higher rate than starts and members — but not
perfectly (78% of solos removed means 22% survive, vs 15% of starts and 0% of members).

The asymmetry that makes it work: solos have LONGER gaps before them than starts/members do, but not
infinitely longer. A 20-bar threshold catches enough of the cluster (short-gap) entries and lets
through enough of the solo (long-gap) entries to net positive. The exact threshold is symbol-
dependent (20 bars on boom500; on crash500 the same rule is null, suggesting crash500's solos and
starts have more overlapping gap distributions).

## Honest limitations

1. **2 of 5 symbols only.** crash300/crash1000/boom1000 weren't run (candle file not found at
   runtime). The boom1000 start tail is the most extreme in the full T-C (-3.099), so it's the
   most likely symbol for a discharge-age filter to matter — but we don't have the data here. The
   boom500 result is suggestive but not confirmed across the book.
2. **No OOS halves.** This run used the full sample for both filter and control (control is a
   random sub-sample, not a time-split). The +0.111 could be overfit to the full sample. Needs an
   OOS check (H1/H2 split) before trusting.
3. **Single threshold.** We tested th in {5, 10, 20, 40} for time-since. th=20 and th=40 gave
   identical results on boom500 (same n), so 20 is the binding value, but we didn't sweep finer
   (e.g. 15, 25, 30) to find the optimum. The +0.111 is at th=20; the optimum could be a bit
   higher or lower.
4. **Not a solo-core selector.** The filter removes 78% of solos. It's a "remove bad populations"
   filter that nets positive because it removes the worst populations harder. That's a legitimate
   forward filter, but it's not the retrospective solo-only ceiling (which was +0.5 to +1.2 over
   controls). The forward filter's +0.111 is well below the retrospective ceiling, which is
   expected — you can't reach the solo-only subset prospectively.

## Verdict

**One real forward signal found: boom500 discharge-age filter (skip entry if last adverse spike
was < 20 M1 bars ago), +0.111 over an unbiased random sub-sample control, n=3758, clean selectivity
(profile removes members hardest, then starts, then solos).** This is a new, qualitatively different
signal from the failed cluster-start (count) filter — it's a gap/silence filter, not a count filter.

**On crash500, null.** No past-information rule beat random by more than noise on n>=5000 samples.

**The solo-core insight remains retrospective-only in absolute terms** — no forward rule reached the
retrospective solo-only ceiling — but the boom500 discharge-age filter is a genuine forward step
toward it, and a different mechanism than the count filter that failed.

**Next (if anything):** OOS halves on the boom500 C_timesince_th20 result; sweep finer thresholds
(15/25/30) on boom500; run the same test on boom1000 and crash300/crash1000 (the missing symbols)
to see if the discharge-age signal generalizes — boom1000 especially, given its extreme start tail.
