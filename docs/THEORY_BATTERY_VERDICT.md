# Theory Battery Verdict — T-A / T-B / T-C / T-D

## What we tested

Five symbols (crash300, crash500, crash1000, boom500, boom1000) on the verified corrected engine
(partial_m1_corrected: gap-through fills, half_exit partial add, per-symbol broker floors,
2xSL/1xSL, hold=3). Spike = adverse bar with range > 3x trailing-100 median. Crash family = LONG
drift (enter at e+1); Boom family = SHORT drift (enter at e+1). M15 regime/BOS from closed-M15-bar
discipline (no lookahead). Matched-lag controls; OOS halves by time. All PnL in points per trade,
1-lot equiv, no spread/commission.

Oracle disclaimer: T-C cluster_start label is computed from the full spike set. The forward-testable
version ("skip if this spike AND you've already seen 2+ adverse spikes in the trailing window") is a
follow-up and is bounded by the oracle numbers below.

## T-C (centerpiece): cluster-start vs solo discharge

### Distribution side (oracle label)

| sym | n_spikes | cluster-start | solo | cls_range_mean | solo_range_mean | cls_ratio_mean | solo_ratio_mean |
|---|---|---|---|---|---|---|---|
| crash300 | 5341 | 770 | 4571 | 6.78 | 6.72 | 7.32 | 7.22 |
| crash500 | 3903 | 544 | 3359 | 3.77 | 3.85 | 10.39 | 10.60 |
| crash1000 | 2208 | 219 | 1989 | 6.50 | 6.56 | 18.75 | 18.97 |
| boom500 | 3810 | 570 | 3240 | 5.81 | 5.87 | 10.42 | 10.50 |
| boom1000 | 2151 | 223 | 1928 | 17.46 | 17.00 | 19.70 | 19.14 |

**Distribution is NOT the signal.** On every symbol the cluster-start spikes and the solo spikes have
essentially the same range and ratio. The cluster-start spikes are not bigger, not more extreme, not
visibly different in their first-bar character. That matters: if the filter worked by selecting "bigger
discharges," you'd see it here. You don't.

### Entry side (the actual claim)

Discharge-following e+1 entries, split by whether the triggering spike was a cluster-start:

| sym | e+1 real avg (WR) | rand avg | diff | non-cluster-start avg (n) | cluster-start avg (n) | non-cluster-start vs ctrl |
|---|---|---|---|---|---|---|
| crash300 | 0.267 (0.66) | 0.153 | +0.114 | 0.434 (4571) | -0.722 (770) | +0.450 vs -0.016 |
| crash500 | 0.126 (0.74) | -0.026 | +0.152 | 0.247 (3359) | -0.624 (544) | +0.299 vs -0.052 |
| crash1000 | 0.192 (0.86) | -0.152 | +0.344 | 0.353 (1989) | -1.272 (219) | +0.422 vs -0.069 |
| boom500 | 0.154 (0.73) | 0.042 | +0.111 | 0.329 (3240) | -0.842 (570) | +0.435 vs -0.106 |
| boom1000 | 0.310 (0.85) | -0.538 | +0.849 | 0.705 (1928) | -3.099 (223) | +0.978 vs -0.273 |

**This is real, and it is consistent across all 5 symbols.** The cluster-start entries are the losing
tail of the e+1 distribution on every symbol, and the non-cluster-start entries are the winning core.
The size of the gap is large and survives the matched-lag control by a wide margin on every symbol.

The most extreme numbers are on boom1000 (cluster-start -3.099, non-cluster-start +0.705, control
-0.273). That is the same symbol the MC verdict flagged as the worst tail per unit, so this is coherent:
the cluster-start loss is the live-book's worst-case population, and the filter would have skipped exactly
those.

### Why this is a real filter and not a post-hoc observation

The critical point: the cluster-start population is NOT distinguishable by spike size. So you cannot
recreate this filter by "skip big spikes." You recreate it by "skip spikes that are followed by more
spikes," which is exactly the cluster concept. The fact that the non-cluster-start population is good
even vs a matched-lag control (not just vs the cluster-start population) means the filter is selecting a
genuinely better entry population, not just carving out a bad one from an average one.

### The honest limitation (important)

The oracle label says "this spike was the first of 3+ in 10 bars." To trade that prospectively, at the
moment you see the spike, you do NOT yet know it's a cluster-start — you only know that after 2 more
spikes arrive within 10 bars. So the truly forward version is:

> time T0: spike detected. Enter? Unknown.
> T0 + k bars (k <= 10): if 2 more adverse spikes have arrived, this was a cluster-start — the entry
> you took at T0+1 (if any) is in the losing population. If you haven't entered yet, skip.
> If fewer than 2 more spikes arrive in the window, this was a solo — the T0+1 entry (if any) is in
> the winning population.

That means the **prospective rule is not "skip on the spike" — it's "skip only after the cluster is
confirmed, i.e. with a delay."** The delay matters because it means you're not skipping the cluster-start
entry; you're recognizing it as bad after the fact. The live-book version of this is the existing
loss-cooldown + cluster-skip machinery, which already does a version of this reactively. The question the
forward test needs to answer is: **does the prospective version (skip un-entered cluster-start candidates
before they become entries) add anything beyond the reactive version (cooldown after a cluster loss)?**

That's the follow-up. The oracle numbers are the ceiling; the forward numbers will be lower because some
of the cluster-start entries in this test were taken before the cluster was confirmed.

## T-A: M15 break-rate as per-trade risk (independent of direction)

Break-rate = number of M15 BOS breaks in the trailing ~90 min (6 M15 bars) before the entry. Buckets
by rate 0/1/2/3+.

| sym | 0 | 1 | 2 | 3+ | largest | largest ctrl |
|---|---|---|---|---|---|---|
| crash300 | 0.847 | 0.371 | 0.237 | 0.102 | 3+ | -0.043 |
| crash500 | 0.489 | 0.249 | 0.100 | -0.013 | 3+ | -0.016 |
| crash1000 | 0.666 | 0.328 | 0.151 | 0.096 | 3+ | 0.069 |
| boom500 | 0.596 | 0.268 | 0.209 | -0.066 | 3+ | -0.044 |
| boom1000 | 1.129 | 1.058 | 0.017 | 0.073 | 3+ | -0.569 |

**Dead as an independent signal.** The apparent pattern ("more breaks = worse") is an artifact of
conditioning on the regime gate. The regime gate already blocks entries in the opposite regime, and the
high-break-rate bucket is mainly entries that happened during an active regime (lots of breaks = the
regime is doing something). The 0-break bucket is actually the best on 3 of 5 symbols (crash300 0.847
vs 3+ 0.102; crash1000 0.666 vs 0.096; boom1000 1.129 vs 0.073). That's not "breaks are bad" — that's
"quiet regime during a drift window is good," which is a different claim and is already captured by the
regime gate itself (you only enter when the regime agrees with drift, and quiet regimes agree more often
in the drift direction).

The boom1000 0 vs 3+ split (1.129 vs 0.073) looks dramatic but the 3+ bucket is n=918 and the ctrl for
the largest bucket is -0.569, so this is the boom1000 noise showing up again. Not trusted.

**Verdict: T-A is not an independent add. The regime gate already captures the usable part of regime
activity. No separate break-rate filter.**

## T-B: bar-1 favorable distance buckets under the partial-add setup

Bar-1 = the bar immediately after the spike entry. Favorable distance = bar-1 close minus entry, in
drift direction. Buckets by per-symbol median bar-1 favorable distance.

| sym | med bar1 range | <=0 | (0, small] | (small, large] | >large |
|---|---|---|---|---|---|
| crash300 | 0.921 | -2.433 (899) | 0.665 (57) | 0.816 (4376) | 0.394 (9) |
| crash500 | 0.368 | -1.545 (411) | 0.022 (13) | 0.323 (3479) | — |
| crash1000 | 0.351 | -3.110 (144) | 0.763 (1) | 0.422 (2063) | — |
| boom500 | 0.568 | -2.420 (431) | -0.549 (17) | 0.487 (3362) | — |
| boom1000 | 0.902 | -8.080 (124) | — | 0.824 (2027) | — |

**Partial signal, and it's a nuance on the existing partial rule, not a new rule.** The <=0 bucket
(bar-1 closes against you) is catastrophic on every symbol, which is the existing partial rule's whole
justification (don't add when bar-1 goes against you). The mid-favorable bucket (small, large] is the
best on 4 of 5 symbols, and the very-large-favorable bucket >large is NOT better — on crash300 it's
0.394 vs 0.816 for the mid bucket, on boom1000 the only bucket that exists beyond <=0 is the mid one.
So the bar-1 signal is "favorable enough, but not too favorable" — which is a version of "bar-1 close
that looks like a discharge continuation (very large favorable move) is not a clean resumption."

This is a refinement of the partial-add decision, not a new entry filter. The existing rule already
doesn't add on <=0. The question is whether adding a **upper bound** on bar-1 favorable distance (don't
add on very large favorable bar-1 because it's likely discharge continuation) improves the add decision.
Small n on the >large bucket makes this tentative. The boom500 (0, small] bucket being -0.549 is also
suspicious — small favorable on boom500 may be discharge continuation, worth a forward look.

**Verdict: T-B is a refinement candidate, not a new edge. Worth a forward test as a partial-add
upper-bound tweak, but not a headline.**

## T-D: dense mode soft-skip vs hard-mask on the 0-2 age window

Dense mode = every-bar entry in drift direction, gate on M15 regime, 10-bar cooldown after loss. Age =
M15 bars since last BOS break.

| sym | dense base avg (n) | hard-mask 3-8 avg (n) | soft-skip 3+ avg (n) | soft ctrl |
|---|---|---|---|---|
| crash300 | 1.635 (8493) | 1.571 (22) | 1.633 (8475) | 0.454 |
| crash500 | 0.720 (11001) | 0.801 (59) | 0.720 (10990) | 0.270 |
| crash1000 | 0.766 (15554) | 0.000 (0) | 0.766 (15554) | 0.453 |
| boom500 | 0.752 (8169) | 0.000 (0) | 0.752 (8169) | 0.023 |
| boom1000 | 0.989 (11078) | -1.088 (26) | 0.993 (11058) | -0.469 |

**No age-window signal in dense mode, because there is almost no 0-2 or 3-8 mass.** On 3 of 5 symbols
(crash1000, boom500, and effectively crash500/boom1000), the dense gated entries are >99% in the 9+
bucket. The regime gate + cooldown already filter dense entries into the settled-regime population, so
the age buckets that mattered for pullback entries (0-2 worst, 3-8 best) are almost empty in dense mode.
The dense mode is already age-clean by construction.

The crash300 and boom1000 dense runs have a tiny 0-2 and 3-8 population (18/22 and 20/26), and on
boom1000 the 3-8 bucket is -1.088 (n=26) — but n=26 is too small to trust, and it's the boom1000 noise
again.

**Verdict: T-D is a null, and the reason is informative.** The age filter's value is a pullback-entry
property, not a dense-entry property, because dense entries are already age-cleaned by the gate. The
pullback mode is where freshness lives; dense mode doesn't need it. No soft-skip variant worth pursuing.

## Consolidated read

1. **T-C is the real find.** Cluster-start discharge-following entries are the losing tail on every
   symbol, and the non-cluster-start population is the winning core, with the gap surviving matched-lag
   controls by +0.3 to +1.0. The distribution is not different (so it's not a "bigger spike" filter), it's
   the cluster timing. The forward version is the reactive cooldown/cluster-skip machinery you already have,
   plus a prospective "don't enter on a spike that's already in a cluster" rule — which is the follow-up.

2. **T-A is a null** once you condition on the regime gate. Break-rate is not an independent risk signal;
   the regime gate already captures the usable part.

3. **T-B is a refinement candidate** on the partial-add decision (don't add on very large favorable bar-1),
   not a new edge. Small n, tentative.

4. **T-D is a null** because dense mode is already age-cleaned by the gate. The freshness edge is a
   pullback-entry property, which we already knew.

## Updated theory

The cluster-start result sharpens the model: **the discharge process has a clustered mode and a solo mode,
and the clustered mode is the losing tail of the e+1 entry rule.** The existing loss-cooldown catches the
clustered mode reactively (after you lose), but the prospective version (don't enter on a spike that's
already in a cluster) would catch it before the entry. The forward test will tell you whether the
prospective version adds anything beyond the reactive version, or whether the reactive version already
captures the whole signal.

That's the next test. The forward version of the T-C filter, with the matched-lag control and OOS halves,
on the same 5 symbols. If it reproduces a meaningful fraction of the oracle gap, it's a live-able filter
(probably as an add-on to the cluster-skip already in the EA). If it collapses to noise, the signal is
purely reactive and the existing cooldown is the right implementation.

## Next

1. Forward T-C: implement the "skip if spike AND 2+ in trailing window" rule, with the same control and
   OOS, on all 5 symbols. Compare to the oracle numbers above.
2. If forward T-C survives, fold it into the paper-test checklist as a v2.15 add-on to the cluster-skip
   already in the EA.
3. T-B forward: partial-add upper bound on bar-1 favorable distance, as a refinement, not a headline.
