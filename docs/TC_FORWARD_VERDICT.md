# Forward T-C Verdict — prospective cluster-skip filter

## What we tested

The forward version of the T-C cluster-skip filter: at the close of a discharge spike bar i,
enter at i+1 ONLY IF fewer than K adverse spikes have occurred in the trailing W M1 bars [i-W, i).
K in {2, 3}, W in {10, 15, 20}. Matched-lag controls, OOS halves by time, reverse-time sanity check,
all on the verified corrected engine (partial_m1_corrected: gap-through fills, half_exit partial add,
per-symbol broker floors, 2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.

Comparator: the oracle non-cluster-start subset from the retrospective test (the ceiling — what a
perfect retrospective filter achieves).

## Bugs in the first run that changed the read

### Bug 1: H2 printed as 0.000 on every symbol — wrong

The OOS halves in tc_forward.py reported H2 avg = 0.000 across the board. The diagnosis in
tc_forward_check.py shows this is a reporting bug, not a real zero-PnL result. The second-half
forward entry set is non-empty on every symbol, and the PnLs are real and positive:

| sym | H2 forward n | H2 mean | min | max |
|---|---|---|---|---|
| crash300 | 824 | 0.204 | -18.383 | 2.881 |
| crash1000 | 689 | 0.211 | -15.759 | 1.238 |
| boom500 | 813 | 0.049 | -12.460 | 1.700 |
| boom1000 | 669 | 0.431 | -36.106 | 2.280 |

So the "H2 is zero" read from the first run is invalid. The OOS halves need to be re-run with the
correct control computation before we trust them. For the verdict below I'm using the H1 numbers from
the first run (which are internally consistent) plus the H2 mean from the diagnosis, but neither half's
control-corrected diff is yet trusted.

### Bug 2: the matched-lag control is contaminated — it over-samples the good population

This is the important one. The control draws random bars within ±20 of each filtered entry. Because
the filtered set is dense in non-cluster-start space (the filter removed most cluster-start entries),
the neighborhood around each filtered entry is also dense in non-cluster-start space. The diagnosis
shows the control is essentially 100% oracle-non-cluster-start:

| sym | forward selected entries | forward oracle-non-cluster-start | forward precision | control entries | control oracle-non-cluster-start | control precision |
|---|---|---|---|---|---|---|
| crash300 | 1629 | 527 | 0.324 | 1629 | 1629 (100%) | 1.000 |
| crash1000 | 1398 | 1179 | 0.843 | 1398 | 1398 (100%) | 1.000 |
| boom500 | 1622 | 1052 | 0.649 | 1622 | 1622 (100%) | 1.000 |
| boom1000 | 1330 | 1107 | 0.832 | 1330 | 1330 (100%) | 1.000 |

The control's average is therefore the non-cluster-start average, and the filter's average is a mix
of non-cluster-start and the cluster-start entries it failed to skip. On crash300, the filter's
precision is only 0.324, so it keeps a lot of cluster-start entries, and the control (100%
non-cluster-start) beats it — but that's a control-design artifact, not a filter failure. On the
high-precision symbols (crash1000, boom1000), the filter's precision is 0.84-0.83, so it's close to
the control's population, and the filter's avg should be close to the control's avg — which is what
the first-run numbers roughly show (crash1000 forward diff +0.205, boom1000 forward diff +0.532).

The correct comparison is NOT "filter avg vs control avg" with this control. The correct comparison is:

- **filter precision vs 1.0** — how much of the cluster-start population does the filter leave in?
- **filter recall of the good population** — of the oracle non-cluster-start entries, how many does the
  filter keep?
- **filter avg vs oracle-non-cluster-start avg** — does the filtered set's avg approach the ceiling?

The control should be re-run on a clean baseline: the FULL e+1 set (not the filtered set), so the
control's population is the full population and the filter's value is measured as the difference
between the filtered set and the full set, both vs the same control. That's the design the first-run
numbers used for the full-set control (full e+1 vs its control), and that's the clean design.

## The real forward result (precision/recall, clean comparison)

### Oracle ceiling (retrospective, for reference)

| sym | oracle non-cluster-start avg (n) | oracle cluster-start avg (n) | oracle non-cluster-start vs ctrl |
|---|---|---|---|
| crash300 | 0.434 (4571) | -0.722 (770) | +0.521 vs -0.087 |
| crash500 | 0.247 (3359) | -0.624 (544) | +0.347 vs -0.100 |
| crash1000 | 0.353 (1989) | -1.272 (219) | +0.625 vs -0.272 |
| boom500 | 0.329 (3240) | -0.842 (570) | +0.447 vs -0.118 |
| boom1000 | 0.705 (1928) | -3.099 (223) | +1.222 vs -0.518 |

### Forward filter precision/recall (W=10, K=2 — the central case)

| sym | forward selected (n) | oracle-non-cluster-start in selection | precision | recall (of good pop) | forward avg | full e+1 avg | full e+1 ctrl | full vs ctrl | forward vs oracle-non ceiling |
|---|---|---|---|---|---|---|---|---|---|
| crash300 | 1629 | 527 | 0.324 | 0.115 | 0.238 | 0.267 | 0.017 | +0.250 | 0.238 vs 0.434 (below ceiling) |
| crash500 | — | — | — | — | — | 0.126 | -0.037 | +0.162 | — |
| crash1000 | 1398 | 1179 | 0.843 | 0.593 | 0.184 | 0.192 | -0.077 | +0.269 | 0.184 vs 0.353 (below ceiling) |
| boom500 | 1622 | 1052 | 0.649 | 0.325 | 0.146 | 0.154 | 0.076 | +0.077 | 0.146 vs 0.329 (below ceiling) |
| boom1000 | 1330 | 1107 | 0.832 | 0.574 | 0.324 | 0.310 | -0.432 | +0.743 | 0.324 vs 0.705 (below ceiling) |

crash500 forward numbers were not re-run in the diagnosis; I'm carrying the first-run values only
where they're consistent with the clean design, and flagging crash500 for re-run.

### What the precision/recall says

1. **The filter does select the non-cluster-start population, but imperfectly, and the imperfection
   varies by symbol.** On crash1000 and boom1000, precision is 0.83-0.84 and recall is 0.57-0.59 — the
   filter is doing a decent job of skipping cluster-start entries and keeping non-cluster-start entries.
   On crash300 and boom500, precision is 0.32-0.65 and recall is 0.12-0.33 — the filter is keeping a lot
   of cluster-start entries it should skip.

2. **Even on the good symbols, the forward filter's avg is BELOW the oracle ceiling.** On crash1000,
   forward avg 0.184 vs oracle-non 0.353; on boom1000, 0.324 vs 0.705. That's because the forward filter
   is missing 40-43% of the good population (recall < 1) AND keeping some cluster-start entries (precision
   < 1). So the forward filter is a weakened version of the retrospective filter — which is exactly what
   you'd expect from a rule that has to decide before the cluster is confirmed.

3. **The filter's value over the full e+1 set is mixed.** On crash300, forward avg 0.238 vs full 0.267 —
   the filter does NOT beat the full set; it removes some good entries and keeps some bad ones, netting
   slightly below the full set. On crash1000, forward 0.184 vs full 0.192 — also slightly below. On
   boom500, 0.146 vs 0.154 — slightly below. On boom1000, 0.324 vs 0.310 — slightly ABOVE. So the
   filter's value over the full set is tiny on 4 of 5 symbols, and small-positive on boom1000.

   The full-set comparison is the honest one: the filter removes some entries (the cluster-start ones it
   catches) and keeps others (the cluster-start ones it misses + the non-cluster-start ones it keeps). The
   net effect on the full set's avg is small because the filter's selectivity is weak relative to the
   population.

## The clean control re-design (what to run next)

The matched-lag control on the filtered set is contaminated and should NOT be used. The clean comparison
is:

- **Filter set avg vs full-set avg**, both with their own matched-lag controls on their own populations.
- OR **filter set avg vs a control drawn from the same filtered set's population but with the filter
  disabled** — which is impossible by construction (the filter defines the set).

The first design (filter vs full, each with own control) is what the first-run numbers already did for
the full set (full e+1 vs its control). The filter set's own control was the contaminated one. So the
clean re-run is: for each (W, K), compute filter set avg (with its own matched-lag control drawn from the
filtered set's neighborhood — but we now know that control is contaminated, so instead use the full-set
control as the baseline and compare filter-avg to full-avg directly). Actually the cleanest is:

- **Full e+1 set**, with matched-lag control drawn from the full set's neighborhood (this is the clean
  baseline — full avg vs full control).
- **Filter set**, reported WITHOUT its own control (because the control is contaminated), compared to the
  full-set control as the baseline. So: filter avg vs full control. If filter avg > full control, the
  filter adds value over the baseline.

That's the design: filter avg (no control) vs full-set control (clean). The first-run numbers already
have the full-set control; we just need to NOT use the contaminated filter control. Let me recompute the
clean diffs:

| sym | filter avg (W=10,K=2) | full-set control | clean diff (filter - full control) |
|---|---|---|---|
| crash300 | 0.238 | 0.017 | +0.221 |
| crash1000 | 0.184 | -0.077 | +0.261 |
| boom500 | 0.146 | 0.076 | +0.070 |
| boom1000 | 0.324 | -0.432 | +0.756 |

Wait — these are the diffs vs the full-set control, and they're ALL positive. That's because the full-set
control is the average of the FULL population (including cluster-start entries), which is LOWER than the
filter population (which has fewer cluster-start entries). So the filter beats the full-set control simply
by removing some cluster-start entries — which is the population effect, not a timing effect. This is the
SAME confound as before, just measured against a different control.

The truly clean comparison requires a control that is NOT contaminated by the filter's population selection.
The only way to get that is a control drawn from a population that is independent of the filter — e.g., a
random sub-sample of the full set that is the SAME SIZE as the filter set, drawn without regard to the
filter. Then compare filter avg to that random sub-sample avg. If the filter avg beats the random
sub-sample avg, the filter is selecting a better-than-random subset. That's the clean test.

Let me define it precisely: for each (W, K), draw N random entries from the full e+1 set, where N =
size of the filter set. Run the engine on the random set. Compare filter avg to random avg. Repeat with
multiple seeds. If filter avg > random avg consistently, the filter is selecting a better-than-random
subset of the full population, which is the honest claim.

This is the test to run next. It's the same idea as the matched-lag control but without the contamination
(in the matched-lag control, the random draws are centered on the filtered entries, which biases them
toward the filter's population; in the random sub-sample control, the random draws are from the full set
uniformly, which is unbiased).

## Reverse-time result (reinterpreted)

On reverse-time data, the forward filter selects the oracle non-cluster-start population in reverse:

| sym | reverse forward entries | oracle-non-cluster-start in selection | precision (in reverse) |
|---|---|---|---|
| crash300 | 1624 | 191 of 200 mappable | 0.955 |
| crash1000 | 1397 | 82 of 85 mappable | 0.965 |
| boom500 | 1623 | 164 of 183 mappable | 0.896 |
| boom1000 | 1331 | 68 of 71 mappable | 0.958 |

So on reverse data, the filter's precision is 0.90-0.97 — much higher than on forward data (0.32-0.84).
That's because on reverse data, the "trailing window" before a spike is the "leading window" in real time,
and the leading window after a cluster-start spike is full of cluster spikes — so the filter on reverse data
is actually selecting the entries that are FAR from cluster spikes in real time, which are exactly the
non-cluster-start entries. The reverse-time filter is a DIFFERENT filter than the forward-time filter: in
forward time it selects "entries with few prior spikes"; in reverse time it selects "entries with few future
spikes," which is the non-cluster-start population by construction.

This is the key insight: **the filter's population selection is symmetric — it selects non-cluster-start
entries whether you run it forward or backward, because "few spikes in the trailing window" and "few spikes
in the leading window" both select the non-cluster-start population (just different halves of it).** That
means the filter is NOT a temporal "cluster causes losses" signal in the sense of "the cluster's temporal
structure makes the entry bad." It's a set-selection signal: "entries that are not in a cluster are good;
entries in a cluster are bad; the filter removes the ones in a cluster."

That's the honest version of the oracle result. The cluster-start entries are bad; the non-cluster-start
entries are good; the filter removes some of the bad ones. Whether it removes them early enough to matter
(forward) vs after the fact (retrospective) is the question the forward test was supposed to answer, and
the answer is: **the forward filter removes a fraction of the bad ones, but its selectivity is weak on
crash300/boom500 and moderate on crash1000/boom1000, and even on the good symbols it doesn't reach the
retrospective ceiling because it misses a lot of the good population too.**

## Updated theory

The cluster-start result, reinterpreted: **the discharge process has a clustered mode and a solo mode, and
the clustered mode is the losing tail of the e+1 entry rule — but the "cluster" is defined by the FUTURE
(spikes that come after), not by the PAST (spikes that came before).** The forward filter uses the PAST
(trailing window), so it's an imperfect proxy for the FUTURE-based cluster label. The retrospective filter
uses the FUTURE (the full cluster), so it's perfect. The forward filter's value is limited by how well the
PAST predicts the FUTURE cluster membership — which varies by symbol and is weak on crash300/boom500.

This reframes the live-book question. The existing reactive cluster-skip (skip if 3+ in 10 bars AFTER the
entry) uses the FUTURE, so it's a retrospective filter in practice (you skip subsequent entries after a
cluster is detected). The forward filter (skip if 2+ in the trailing window BEFORE the entry) uses the
PAST, so it's a prospective filter with imperfect selectivity. The question for the live book is: **does
the prospective filter add anything beyond the reactive skip?** The reactive skip already removes the FUTURE
cluster entries (the ones after the first spike). The prospective filter would remove the FIRST spike of the
cluster (the one that starts it). So the prospective filter's value is specifically about not taking the
cluster-STARTING entry, which the reactive skip does NOT remove (the reactive skip removes the 2nd, 3rd, etc.
entries, not the 1st).

So the prospective filter and the reactive skip are complementary: the reactive skip removes the cluster
MEMBER entries; the prospective filter removes the cluster STARTING entry. The prospective filter's value is
the cluster-starting entry's loss (-0.7 on crash300, -0.6 on crash500, -1.3 on crash1000, -0.8 on boom500,
-3.1 on boom1000), IF the prospective filter can identify it early enough. The forward test shows the
prospective filter identifies it with precision 0.32-0.84 and recall 0.12-0.59, so it captures a fraction
of that loss. The net value is the captured fraction of the cluster-start loss, minus the good entries it
wrongly removes (which is the recall loss on the good population).

On crash300, the cluster-start loss is -0.722 on 770 entries = -556 points total. The forward filter
removes 1629 entries, of which 527 are good (recall 0.115 of 4571 good) and 1102 are bad (it removed 1102
of 770 cluster-start? No — it removed 1629 entries total, of which 527 are good and 1102 are bad, but there
are only 770 cluster-start entries total, so it removed at most 770 bad entries and 859 good entries... wait,
let me recompute. The forward filter selected 1629 entries to KEEP, not to remove. It removed 5341 - 1629 =
3712 entries. Of the 1629 kept, 527 are good and 1102 are bad? No — precision 0.324 means 527 of 1629 kept
are good, so 1102 of 1629 kept are bad. That means the filter KEPT 1102 cluster-start entries (out of 770
total cluster-start — impossible, 1102 > 770). So precision 0.324 means 527 of 1629 kept are oracle-non-
cluster-start, and 1102 of 1629 kept are NOT oracle-non-cluster-start — but "not oracle-non-cluster-start"
includes both cluster-start AND entries that are neither (the oracle label is binary: cluster-start or not).
So 1102 kept entries are "not non-cluster-start" = cluster-start OR borderline. Since there are only 770
cluster-start entries total, at most 770 of the 1102 are cluster-start; the rest are borderline (entries that
are in a cluster but not the first one — i.e., cluster MEMBERS that the forward filter kept because they had
few prior spikes).

OK this is getting into the weeds. The point is: the forward filter's selectivity is messy, and the clean
re-run with the unbiased random sub-sample control is the right next step. I'll define that test and run it.

## Next

1. **Clean forward re-run with unbiased random sub-sample control.** For each (W, K), draw N random entries
   from the full e+1 set (N = filter set size), run the engine, compare filter avg to random avg across
   multiple seeds. This is the honest test of whether the filter selects a better-than-random subset.
2. **Re-run the OOS halves with the clean design** (filter avg vs full-set control, and filter avg vs random
   sub-sample control in each half).
3. **Re-run crash500** (missing from the diagnosis).
4. **Quantify the complementary value** of the prospective filter vs the reactive skip: the prospective
   filter removes the cluster-STARTING entry; the reactive skip removes the cluster MEMBER entries. The
   prospective filter's value is the cluster-start loss captured, which is the precision-weighted cluster-start
   loss. On crash300, if the filter keeps 1629 entries of which 1102 are "not non-cluster-start" (cluster-start
   + members), then it removed 3712 entries of which 770 - (1102 - (770 - removed_cluster_start)) ... this is
   getting too complicated for a back-of-envelope. The clean way is to label each forward-entry decision as
   "cluster-start," "cluster-member," or "solo," and compute the filter's removal rate for each. That's the
   next diagnostic.

Let me run the clean re-run first and the cluster-label diagnostic second.
