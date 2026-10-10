# Clean Forward T-C Verdict — unbiased random sub-sample control + cluster-label diagnostic

## What we tested

Forward filter: at spike close i, enter at i+1 only if fewer than K adverse spikes in trailing W M1 bars
[i-W, i). K in {2, 3}; W in {10, 15, 20}. Control: UNBIASED random sub-sample of the FULL e+1 set, same
size as the filter set, 5 seeds (mean and range reported). Cluster-label diagnostic: each spike labeled
cluster-start (first of >=3 in 10 bars), cluster-member (in cluster but not first), or solo (not in any
>=3 cluster). For each filter: removal rate per label, kept-avg per label.

Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors, 2xSL/1xSL, hold=3).
PnL in points per trade, 1-lot equiv, no spread/commission.

## Full-population label means (the structure the filter is working within)

| sym | solo mean (n) | member mean (n) | start mean (n) | full avg |
|---|---|---|---|---|
| crash300 | 1.671 (947) | 0.111 (3624) | -0.722 (770) | 0.267 |
| crash500 | 0.665 (1346) | -0.032 (2013) | -0.624 (544) | 0.126 |
| crash1000 | 0.597 (1432) | -0.274 (557) | -1.272 (219) | 0.192 |
| boom500 | 1.000 (1262) | -0.099 (1978) | -0.842 (570) | 0.154 |
| boom1000 | 1.345 (1355) | -0.810 (573) | -3.099 (223) | 0.310 |

This is the key structure: **solo entries are the best population on every symbol; cluster-start entries
are the worst; cluster-members are in between (slightly positive on crash300, slightly negative elsewhere).**
The reactive cluster-skip removes cluster-members; the prospective filter was supposed to remove cluster-starts.
The clean test asks whether the forward filter actually does that, or whether it removes the wrong population.

## Clean forward results (filter avg vs unbiased random sub-sample)

### crash300

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1629 | 0.238 | 0.223 | [0.154, 0.302] | +0.016 | 0.283 | 0.950 | 0.000 |
| 10 | 3 | 3715 | 0.265 | 0.260 | [0.235, 0.292] | +0.005 | 0.000 | 0.449 | 0.000 |
| 15 | 2 | 839 | 0.160 | 0.224 | [0.137, 0.452] | -0.064 | 0.602 | 1.000 | 0.400 |
| 15 | 3 | 2518 | 0.315 | 0.248 | [0.201, 0.345] | +0.068 | 0.086 | 0.741 | 0.073 |
| 20 | 2 | 429 | 0.113 | 0.215 | [0.045, 0.426] | -0.103 | 0.805 | 1.000 | 0.683 |
| 20 | 3 | 1574 | 0.227 | 0.219 | [0.129, 0.305] | +0.008 | 0.373 | 0.874 | 0.321 |

### crash500

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1651 | 0.110 | 0.132 | [0.057, 0.175] | -0.021 | 0.267 | 0.940 | 0.000 |
| 10 | 3 | 3140 | 0.130 | 0.117 | [0.079, 0.146] | +0.012 | 0.000 | 0.379 | 0.000 |
| 15 | 2 | 1035 | 0.150 | 0.116 | [0.039, 0.184] | +0.034 | 0.509 | 1.000 | 0.312 |
| 15 | 3 | 2474 | 0.126 | 0.126 | [0.089, 0.153] | -0.001 | 0.049 | 0.666 | 0.042 |
| 20 | 2 | 656 | 0.071 | 0.125 | [0.053, 0.193] | -0.054 | 0.693 | 1.000 | 0.553 |
| 20 | 3 | 1869 | 0.132 | 0.134 | [0.076, 0.163] | -0.003 | 0.227 | 0.812 | 0.173 |

### crash1000

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1398 | 0.184 | 0.209 | [0.172, 0.246] | -0.026 | 0.199 | 0.943 | 0.000 |
| 10 | 3 | 2049 | 0.173 | 0.203 | [0.190, 0.213] | -0.031 | 0.000 | 0.285 | 0.000 |
| 15 | 2 | 1073 | 0.147 | 0.216 | [0.156, 0.260] | -0.069 | 0.377 | 1.000 | 0.174 |
| 15 | 3 | 1878 | 0.157 | 0.199 | [0.176, 0.231] | -0.041 | 0.020 | 0.533 | 0.018 |
| 20 | 2 | 830 | 0.144 | 0.183 | [0.135, 0.231] | -0.039 | 0.516 | 1.000 | 0.374 |
| 20 | 3 | 1682 | 0.164 | 0.207 | [0.152, 0.251] | -0.043 | 0.091 | 0.684 | 0.068 |

### boom500

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1622 | 0.146 | 0.181 | [0.090, 0.273] | -0.035 | 0.259 | 0.941 | 0.000 |
| 10 | 3 | 3069 | 0.159 | 0.157 | [0.132, 0.186] | +0.003 | 0.000 | 0.375 | 0.000 |
| 15 | 2 | 1036 | 0.099 | 0.184 | [0.042, 0.335] | -0.085 | 0.494 | 1.000 | 0.302 |
| 15 | 3 | 2425 | 0.161 | 0.147 | [0.111, 0.175] | +0.014 | 0.048 | 0.661 | 0.032 |
| 20 | 2 | 637 | 0.103 | 0.137 | [0.004, 0.281] | -0.034 | 0.689 | 1.000 | 0.570 |
| 20 | 3 | 1846 | 0.117 | 0.167 | [0.109, 0.243] | -0.050 | 0.219 | 0.794 | 0.204 |

### boom1000

| W | K | filter n | filter avg | random mean | random range | clean diff | solo removal | member removal | start removal |
|---|---|---|---|---|---|---|---|---|---|
| 10 | 2 | 1330 | 0.324 | 0.315 | [0.232, 0.443] | +0.010 | 0.210 | 0.935 | 0.000 |
| 10 | 3 | 1982 | 0.254 | 0.317 | [0.287, 0.395] | -0.064 | 0.000 | 0.295 | 0.000 |
| 15 | 2 | 1020 | 0.251 | 0.342 | [0.197, 0.456] | -0.091 | 0.380 | 1.000 | 0.193 |
| 15 | 3 | 1811 | 0.260 | 0.293 | [0.200, 0.385] | -0.034 | 0.021 | 0.545 | 0.000 |
| 20 | 2 | 790 | 0.259 | 0.335 | [0.231, 0.522] | -0.075 | 0.528 | 1.000 | 0.323 |
| 20 | 3 | 1625 | 0.182 | 0.297 | [0.217, 0.405] | -0.114 | 0.098 | 0.661 | 0.063 |

## What the clean test says

### Headline: the forward filter is a cluster-MEMBER remover, not a cluster-START remover

On every symbol at W=10 K=2 (the central case), the filter removes 0% of cluster-start entries (removal
rate 0.000 on crash300, crash500, crash1000, boom500, boom1000) and removes 94-95% of cluster-members.
That's the OPPOSITE of what the prospective filter was supposed to do. The filter is removing the 2nd, 3rd,
etc. spikes of a cluster (the members), not the 1st spike (the start). The reactive cluster-skip already
removes members. So the forward filter at W=10 K=2 is NOT complementary to the reactive skip — it's doing
the same job, and doing it by a different (worse) mechanism.

### Why it removes members, not starts

The forward filter's decision variable is "count of adverse spikes in the trailing W bars." A cluster-start
spike has FEW prior spikes (it's the first of the cluster), so the filter KEEPS it. A cluster-member spike
has MANY prior spikes (the start and possibly other members), so the filter REMOVES it. That's the opposite
of the intended logic: the filter was designed to remove cluster-start entries (which have few prior spikes
and thus look like solo entries to the trailing-window rule), but it actually removes cluster-member entries
(which have many prior spikes). The filter's decision variable is anti-correlated with cluster-start status
and correlated with cluster-member status.

This is the core finding: **the trailing-window spike count is a member-detector, not a start-detector.** The
filter removes entries that are IN a cluster (members), not entries that START a cluster (starts). The start
entries are the ones the filter keeps, and the start entries are the worst population (-0.722 on crash300,
-0.624 on crash500, -1.272 on crash1000, -0.842 on boom500, -3.099 on boom1000). So the filter keeps the
worst population and removes the better population (members are -0.032 to -0.810, better than starts on
every symbol).

### The clean diff confirms it

The clean diff (filter avg - random sub-sample mean) is near zero or negative on 29 of 30 configurations.
The only positives are:

- crash300 W=15 K=3: +0.068 (but start removal only 0.073, member removal 0.741, solo removal 0.086)
- crash500 W=15 K=2: +0.034 (but start removal 0.312, solo removal 0.509 — eating good entries)
- boom500 W=10 K=3: +0.003 (essentially zero)
- boom500 W=15 K=3: +0.014 (weak)
- boom1000 W=10 K=2: +0.010 (essentially zero)
- crash300 W=10 K=2: +0.016 (essentially zero)
- crash300 W=10 K=3: +0.005 (essentially zero)
- crash300 W=20 K=3: +0.008 (essentially zero)
- crash500 W=10 K=3: +0.012 (essentially zero)

These positives are all small (0.003 to 0.068) and are either config-specific noise or the filter's member-
removal benefit (removing the slightly-negative member population) partially offsetting the start-keeping cost.
The crash300 W=15 K=3 +0.068 is the largest, but it's still small in absolute terms (filter avg 0.315 vs
random 0.248, difference 0.068 on a base of 0.267 full avg), and the mechanism is mixed (member removal 0.741,
solo removal 0.086, start removal 0.073 — it's removing members and some solos and a few starts, netting a small
positive). This is not a clean cluster-start filter; it's a member-solo mix with weak start removal.

### The kept-avg per label confirms the filter keeps the worst population

Filtered-set kept-avg per label at W=10 K=2 (central case):

| sym | solo kept-avg | member kept-avg | start kept-avg |
|---|---|---|---|
| crash300 | 1.351 | 0.147 | -0.722 |
| crash500 | 0.533 | -0.039 | -0.624 |
| crash1000 | 0.492 | -0.895 | -1.272 |
| boom500 | 0.794 | -0.221 | -0.842 |
| boom1000 | 1.099 | -1.432 | -3.099 |

The filter keeps ALL cluster-start entries (770/770, 544/544, 219/219, 570/570, 223/223), and the kept-avg
for starts is the full-start avg (because it keeps all of them). The filter keeps a subset of solo entries
(679/947 on crash300, etc.) with slightly lower solo-avg than the full solo avg (1.351 vs 1.671 on crash300 —
the filter removed the best solo entries). The filter keeps a tiny subset of members (180/3624 on crash300) with
similar member-avg. So the filter is removing the BEST solo entries and the MOST members, and keeping ALL starts.
That's the worst possible selectivity for the stated goal (remove starts, keep solos).

## Why the retrospective filter looked so good and the forward filter looks so bad

The retrospective filter (oracle non-cluster-start subset) removes all cluster-start entries AND all cluster-
members (because the non-cluster-start subset is the solo entries only). That's a clean solo-only subset, which
is the best population on every symbol (solo avg 0.6-1.7 vs full avg 0.1-0.3). The retrospective filter's value
is entirely the solo-population selection: it removes starts AND members, keeping only solos.

The forward filter, by contrast, keeps ALL starts (because starts have few prior spikes) and removes most
members (because members have many prior spikes). It's a member-remover, not a start-remover. The forward
filter's value, if any, comes from removing the slightly-negative member population, which is a much smaller
effect than removing the strongly-negative start population. On crash300, the member population is +0.111 (slightly
positive), so removing it is actually costly; on boom500/boom1000, the member population is -0.1 to -0.8, so
removing it helps a bit. The net effect is small and symbol-dependent.

## Updated theory: what the cluster structure actually is

The cluster structure, reinterpreted from the label diagnostic:

- **Solo entries (not in any >=3 cluster):** the best population on every symbol. These are the "clean drift"
  entries that the e+1 rule is designed for. They are ~18-25% of the full population on crash300/crash1000/
  boom500/boom1000 and ~25% on crash500.
- **Cluster-member entries (2nd, 3rd, ... spike of a >=3 cluster):** intermediate. Slightly positive on
  crash300 (+0.111), slightly negative on the others (-0.032 to -0.810). These are the entries the reactive
  cluster-skip removes. They are ~37-68% of the full population (the majority on crash300/crash500/boom500).
- **Cluster-start entries (1st spike of a >=3 cluster):** the worst population on every symbol. These are the
  entries the prospective filter was supposed to remove. They are ~4-14% of the full population (770/5341 on
  crash300, 544/3903 on crash500, 219/2208 on crash1000, 570/3810 on boom500, 223/2151 on boom1000).

So the full population is mostly members (the reactive skip's job) with a small start tail (the prospective
filter's target) and a smaller solo core (the best population). The e+1 rule's full avg is dragged down by the
start tail and buoyed by the solo core, with the member majority in between.

The retrospective filter's value is removing both the start tail AND the member majority, leaving only the solo
core — which is a large value because the solo core is much better than the full population. The prospective
filter's value, if it worked, would be removing only the start tail — which is a smaller value because the start
tail is small (4-14% of the population) even though it's the worst per-trade population.

## The prospective filter's complementary value vs the reactive skip

The reactive skip removes cluster-members (the 2nd, 3rd, ... spikes). The prospective filter was supposed to
remove cluster-starts (the 1st spike). If the prospective filter worked, it would be complementary: the reactive
skip handles the members, the prospective filter handles the starts, and together they remove the entire cluster
population, leaving only solos — same as the retrospective filter, but done in two steps (one reactive, one
prospective).

The clean test shows the prospective filter does NOT work as designed: it removes members (like the reactive
skip) and keeps starts (the opposite of its target). So the prospective filter is NOT complementary to the
reactive skip — it's redundant with it (both remove members) and counterproductive (it keeps starts).

For the prospective filter to be complementary, it would need a decision variable that is HIGH for starts and
LOW for members — i.e., the opposite of the trailing-window spike count. What would that be? A start is the FIRST
spike of a cluster, so it has few prior spikes (low trailing count) AND few future spikes before the cluster
builds (the cluster hasn't started yet). A member has many prior spikes (high trailing count) and is in the
middle of a cluster. So the trailing count distinguishes members from starts in the RIGHT direction for members
(high count = member) but in the WRONG direction for starts (low count = start, but also low count = solo —
starts and solos both have low trailing counts, and the filter can't tell them apart).

The filter CANNOT distinguish starts from solos using only the trailing spike count, because both have few prior
spikes. To distinguish them, you'd need a forward-looking variable: "will this spike be followed by 2+ more
spikes in the next W bars?" — which is the future, which you can't know at decision time. So the prospective
filter, as a pre-entry rule using only past information, CANNOT target cluster-starts specifically. It can only
target cluster-members (which have past information), and that's what it does.

## This is the honest answer to the forward T-C question

The prospective cluster-start filter is NOT implementable as a pre-entry rule using only past information,
because cluster-starts are defined by future spikes (the spikes that come after), and the past information
(trailing spike count) is anti-correlated with start status (starts have low trailing counts, which the filter
interprets as "keep"). The filter ends up removing cluster-members (which the reactive skip already removes) and
keeping cluster-starts (which is the wrong population), with a small net effect that is near zero or negative on
29 of 30 configurations.

The retrospective filter's value is real (solo-only subset, +0.5 to +1.2 over controls), but it's a retrospective
filter (you need to know the future to know which entries are solo). The forward filter's value is near zero
because it can't target the same population prospectively.

## What this means for the live book

1. **The reactive cluster-skip is the right implementation of the cluster defense.** It removes cluster-members,
   which is the majority of the cluster population and the reactive skip's natural target. Keep it.
2. **The prospective cluster-start filter is not implementable as designed.** The trailing-window spike count is
   a member-detector, not a start-detector. Don't build it as a pre-entry filter — it would keep the worst
   population (starts) and remove the better population (members), which is the opposite of the goal.
3. **The retrospective filter's value (solo-only subset) is the ceiling, and it's not prospectively achievable**
   with a simple past-information rule. The solo-only subset is the best population, but you can't identify solos
   prospectively without knowing the future cluster membership. The best you can do prospectively is the member-
   removal (which the reactive skip does) plus keeping as many solos as possible (which means NOT removing entries
   with low trailing spike counts — i.e., NOT applying the forward filter at all, because the forward filter removes
   low-trailing-count entries, which are the solos AND the starts, and it can't tell them apart).

So the honest recommendation: **don't add the prospective cluster-start filter to the live book.** The reactive
cluster-skip is the right defense. The retrospective filter's value is the solo-population ceiling, which is a
post-hoc analysis insight (the solo entries are the best population), not a prospective filter.

## What IS prospectively achievable (the real forward takeaway)

The label diagnostic gives us a prospective insight that IS achievable: **solo entries (low trailing spike count
AND low future spike count) are the best population, and you can't identify them prospectively, but you CAN avoid
the start population prospectively by NOT entering on spikes that have low trailing spike counts?** No — that's
the opposite, because starts also have low trailing counts. You can't avoid starts prospectively with past info.

What you CAN do prospectively, and what the label diagnostic suggests: **avoid entering on spikes that have HIGH
trailing spike counts** (which are members, and the reactive skip already handles them) — but that's the reactive
skip. OR **avoid entering on spikes that have HIGH trailing spike counts AND are in the drift direction** — which
is the reactive skip plus a direction filter. Nothing new.

The real prospective insight from the label diagnostic is negative: **there is no simple past-information rule that
isolates the solo population prospectively**, because solos and starts both have low trailing spike counts. The
solo population's superior performance is a retrospective observation, not a prospective filter.

## Next (if anything)

The only thing the clean test suggests that might be worth a forward look: the crash300 W=15 K=3 configuration
has a small positive clean diff (+0.068) with a relatively clean selectivity profile (solo removal 0.086, member
removal 0.741, start removal 0.073 — keeps most solos, removes most members, barely touches starts). But the
start removal is still only 7%, so it's not capturing the start population, and the positive diff is mostly the
member-removal benefit (removing the slightly-positive member population on crash300 is actually a cost, so the
positive diff is surprising and may be noise). Worth a forward look with OOS halves and a clean control to see if
it's real, but it's not the cluster-start filter — it's a member-removal config that happens to have a small positive
on crash300.

Actually, removing the slightly-positive member population on crash300 should be a COST, not a benefit, so the
+0.068 is suspicious. Let me check: crash300 member mean is +0.111 (slightly positive). Removing members (removing
the +0.111 population) should LOWER the filter avg, not raise it. But the filter at W=15 K=3 removes 74% of members
and the filter avg is +0.315 vs random +0.248. The filter also removes 8.6% of solos (the +1.671 population) and
7.3% of starts (the -0.722 population). Removing solos costs, removing starts helps, removing members costs (on
crash300). The net is +0.068, which means the start removal (7.3% of the -0.722 population removed = removing
some negative entries) outweighs the solo removal cost and the member removal cost. But 7.3% start removal is small,
and the start population is only 770 entries, so 7.3% = 56 entries removed, at -0.722 each = +40 points removed
(positive for the filter). The solo removal is 8.6% of 947 = 81 entries removed, at +1.671 each = -135 points
removed (negative for the filter). The member removal is 74% of 3624 = 2681 entries removed, at +0.111 each =
-297 points removed (negative for the filter). So the net is +40 - 135 - 297 = -392 points removed, which would
LOWER the filter avg, not raise it. But the filter avg is +0.315 vs random +0.248, which is HIGHER. So the +0.068
is NOT explained by the label removal rates — there's something else going on. The kept-avg per label: solo 1.670
(kept solos are as good as full solos), member -0.192 (kept members are worse than full members +0.111), start -0.662
(kept starts are slightly better than full starts -0.722). So the filter keeps the BEST solos, the WORST members,
and the BEST starts. The kept set is a selected subset that is better than the random sub-sample on each label, which
is why the filter avg is higher than random. But this is selection within the kept set, not removal of the bad
population — it's keeping the best of each label, which is a different mechanism than removing cluster-starts.

This is getting too deep into config-specific noise. The bottom line is clear: **the forward cluster-start filter is
not implementable as designed, and the clean test confirms it's near zero or negative on 29 of 30 configurations.**
The crash300 W=15 K=3 +0.068 is a config-specific selection effect, not a cluster-start filter, and is not worth
pursuing without OOS confirmation.

## Verdict

**The prospective cluster-start filter is not implementable as a pre-entry rule using only past information.** The
trailing-window spike count is a member-detector, not a start-detector. The clean test (unbiased random sub-sample
control + cluster-label diagnostic) shows the filter removes cluster-members (redundant with the reactive skip) and
keeps cluster-starts (the wrong population), with a near-zero or negative clean diff on 29 of 30 configurations.
The retrospective filter's value (solo-only subset, +0.5 to +1.2 over controls) is real but not prospectively
achievable with a simple past-information rule, because solos and starts both have low trailing spike counts and
can't be distinguished prospectively.

**Recommendation: don't add the prospective cluster-start filter to the live book.** The reactive cluster-skip is
the right defense. The retrospective filter's value is a post-hoc insight (the solo entries are the best population),
not a prospective filter. The only thing worth a forward look is the crash300 W=15 K=3 config (weak positive,
member-removal-driven, not a cluster-start filter), and only with OOS confirmation — but it's not the filter we set
out to build.
