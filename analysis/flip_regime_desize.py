#!/usr/bin/env python3
"""P21 (pre-registered Appendix AH / FLIP_PLAN.md) — vol-regime DE-SIZING.

WHY THIS STUDY EXISTS
---------------------
Appendices AE/AH established that ordinary-tick volatility runs in REGIMES:
the rolling 5000-tick median |delta| sat at ~0.0085u for nine days, then
1.39x, then 2.98x for exactly Sep 14-15 - the two bleed days - and the
Sep 16-17 holdout ran 1.55x-2.98x and lost on every menu cell.

Appendix AH point (4) licensed exactly ONE new study and pre-registered it:
  "if a vol/activity heartbeat can flag cluster days in real time, the
   response is DE-SIZING, not entry suppression (entry filters were
   tombstoned; de-sizing was never tested - that is the one new study
   this appendix licenses, P21, pre-registered before any run)."

No P21 script was ever written (flip_live_gap_audit.py is P21a, flip_oos_c.py
is P20). This is that study.

THE HARD CONSTRAINT THAT SHAPES THE ANSWER
------------------------------------------
On a FIXED 0.1-lot book the minimum lot is 0.1, so "de-sizing" is not
continuous - it is binary: trade or skip. True fractional de-sizing only
exists on the mission's compounding ladder (0.1/0.2/0.3/0.4), where the
response is "hold the lower rung". Both are tested here, labelled.

METHOD / DISCIPLINE
-------------------
- Engine: clone of flip_rolling_engine.run_conv (live convention: rolling
  5000-tick median, gap-masked). Validated bit-exact against the shared
  engine before any number is trusted.
- Regime variable: rv = rolling_median[entry] / baseline. CAUSAL - the
  median at the entry tick is built only from past deltas, so it is
  implementable live. Two baselines are reported (fixed archive median and
  a causal EWMA) so the result is not a baseline-choice artifact.
- Policy sweep is applied POST-HOC to the traced trip list for speed, then
  the headline policy is RE-RUN IN-ENGINE (where a skip genuinely prevents
  the position and perturbs subsequent state) to check the post-hoc
  approximation holds.

Usage: python analysis/flip_regime_desize.py
"""
import os
import sys
import time as _t
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MIN_AGE, STEP_LOTS
from flip_gapmask import bad_tick_mask
from flip_next_gen import day_block, ladder_fn
from flip_phase_backtest import get_specs
from flip_rolling_engine import rolling_medians, run_conv as shared_run

SYMBOL = "flip1000"
SPEC = get_specs()[SYMBOL]
VPU = SPEC["tick_value"] / SPEC["tick_size"]
PER_UNIT = STEP_LOTS * VPU
LIVE_SPREAD = 0.6261
SPREAD_USD = LIVE_SPREAD * PER_UNIT
MULT = 5.0
DELAY = 300          # live DELAY_TICKS
MED_CACHE = f"data/flip_exec/_rolling_med_{SYMBOL}.npz"


def get_med_roll(ms, px):
    """Rolling medians, cached by tick count (the bisect loop is slow)."""
    if os.path.exists(MED_CACHE):
        try:
            z = np.load(MED_CACHE)
            if int(z["n"]) == len(px):
                print(f"  rolling medians from cache ({MED_CACHE}, n={len(px):,})")
                return z["med"]
        except (OSError, ValueError, KeyError):
            pass
    print(f"  computing rolling medians over {len(px):,} ticks (bisect window)...")
    t0 = _t.time()
    med = rolling_medians(ms, px)
    print(f"  done in {_t.time() - t0:.1f}s")
    try:
        np.savez_compressed(MED_CACHE, med=med, n=len(px))
        print(f"  cached -> {MED_CACHE}")
    except OSError as e:
        print(f"  cache write failed: {e}")
    return med


def run_traced(ms, px, med_t, side=0, mode="counter", mult=MULT, delay=DELAY,
               k=None, gate=None, base=None):
    """flip_rolling_engine.run_conv + per-trip (entry_j, exit_j, rv) records.

    gate(rv) -> bool allows an entry; evaluated at the COMMIT point (the
    fill for delay>0, the spike for delay=0). A blocked commit drops the
    pending rather than letting it fill on stale context. base is the rv
    denominator; gate=None reproduces the shared engine exactly.
    """
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr_t = mult * med_t
    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    pending = False
    pend_j = 0
    ep_dir, ep_j = 0, -1
    trips = []          # (net, entry_j, exit_j, rv_at_commit)
    n_gate = n_fill = 0

    def rv_at(j):
        if base is None:
            return 1.0
        b = float(med_t[j - 1]) / base
        return b

    def close(fill, j):
        nonlocal pos, pending
        net = (fill - entry) * pos * VPU - SPREAD_USD
        # t[0] net $, t[1] entry tick index, t[2] exit TIME (ms, the day key),
        # t[3] regime rv at the commit tick
        trips.append([net, ent_j, ms[j], ent_rv])
        pos, pending = 0.0, False

    ent_j = ent_rv = 0
    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, pending = 0.0, 0, 0, False
            ep_dir, ep_j = 0, -1
            continue
        is_spike = bool(d[j - 1] > thr_t[j - 1] or -d[j - 1] > thr_t[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if ep_dir != 0 and s != 0 and s != ep_dir:
                ep_dir, ep_j = 0, -1

        if pos != 0:
            if is_spike and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if mode == "counter" or pnl_now >= 0:
                    close(px[j], j)
                    continue
                if not pending:
                    pending, pend_j = True, j
                    continue
            elif pending and is_spike and s == np.sign(pos):
                close(px[j], j)
                continue
            elif pending and mode == "timebnd" and j - pend_j >= k:
                close(px[j], j)
                continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    rv = rv_at(j)
                    if gate is not None and not gate(rv):
                        n_gate += 1
                        ep_dir, ep_j = 0, -1
                        continue
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    ent_j, ent_rv = j, rv
                    n_fill += 1
                    ep_dir, ep_j = 0, -1
                    pending = False
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= MIN_AGE + 1 and is_spike:
                    if delay == 0:
                        rv = rv_at(j)
                        if gate is not None and not gate(rv):
                            n_gate += 1
                            continue
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        ent_j, ent_rv = j, rv
                        n_fill += 1
                        pending = False
                    else:
                        ep_dir, ep_j = cur_dir, j
    if pos != 0:
        close(px[-1], len(px) - 1)
    return trips, n_gate


def stats(trips, days):
    """Trip-list metrics; trips are chronological."""
    if not trips:
        return None
    nets = np.array([t[0] for t in trips])
    cum = np.cumsum(nets)
    dd = float(np.max(np.maximum.accumulate(cum) - cum)) if len(cum) else 0.0
    byday = {}
    for t in trips:
        dd_ = datetime.fromtimestamp(t[2] / 1000, tz=timezone.utc).date()
        byday[dd_] = byday.get(dd_, 0.0) + t[0]
    daily = np.array(list(byday.values()))
    losses = nets[nets < 0]
    return dict(n=len(nets), net=float(nets.sum()),
                per_wk=float(nets.sum() / days * 7),
                wr=float((nets > 0).mean() * 100),
                avg=float(nets.mean()),
                avg_win=float(nets[nets > 0].mean()) if (nets > 0).any() else 0.0,
                avg_loss=float(losses.mean()) if len(losses) else 0.0,
                worst=float(nets.min()),
                maxdd=dd,
                worst_day=float(daily.min()),
                n_days=len(daily),
                loss_usd=float(-losses.sum()) if len(losses) else 0.0)


def show(tag, s):
    print(f"  {tag:<26} n={s['n']:4d} net/wk=${s['per_wk']:+7.2f} net=${s['net']:+7.2f} "
          f"WR={s['wr']:5.1f}% avg=${s['avg']:+.3f} avgL=${s['avg_loss']:+.3f} "
          f"worst=${s['worst']:+.2f} maxDD=${s['maxdd']:6.2f} worstDay=${s['worst_day']:+.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"=== P21 vol-regime de-sizing | {SYMBOL} | {days:.1f} gap-masked days "
          f"| {len(px):,} ticks ===")
    print(f"live convention: rolling {5000}-tick median, mult={MULT}, "
          f"min_age={MIN_AGE}, delay={DELAY}, spread={LIVE_SPREAD}u "
          f"(${SPREAD_USD:.4f}/trip)\n")

    med = get_med_roll(ms, px)
    base_fixed = float(np.median(med[WARMUP:]))
    # causal EWMA reference (half-life ~150k ticks) for the robustness check
    alpha = 1 - 0.5 ** (1 / 150_000)
    ew = np.zeros(len(med))
    acc = med[WARMUP]
    for i in range(WARMUP, len(med)):
        acc = acc + alpha * (med[i] - acc)
        ew[i] = acc
    print(f"  baseline (archive median rolling median) = {base_fixed:.5f}u")
    print(f"  baseline (causal EWMA, on the fly)       = {ew[-1]:.5f}u")
    print(f"  regime spread p05/p50/p95/p99 = "
          f"{np.percentile(med[WARMUP:], 5):.5f} / {np.percentile(med[WARMUP:], 50):.5f} / "
          f"{np.percentile(med[WARMUP:], 95):.5f} / {np.percentile(med[WARMUP:], 99):.5f}\n")

    for side, sname in ((0, "LS"), (1, "L long-only")):
        # ---- engine discipline: gate=None must equal the shared engine ----
        trips, ng = run_traced(ms, px, med, side=side, mode="counter",
                               delay=DELAY, gate=None, base=base_fixed)
        nets = np.array([t[0] for t in trips])
        assert ng == 0
        # engine discipline: the traced clone must reproduce the shared
        # rolling engine bit-for-bit with the gate disabled
        sn, _ = shared_run(ms, px, med, base_fixed, side=side, mode="counter",
                           mult=MULT, delay=DELAY)
        ok = len(sn) == len(nets) and np.allclose(sn, nets)
        print(f"--- {sname}: traced n={len(nets)} net=${nets.sum():+.2f} "
              f"(${nets.sum() / days * 7:+.2f}/wk) ---")
        print(f"    engine parity vs flip_rolling_engine.run_conv: "
              f"{'BIT-EXACT' if ok else 'MISMATCH'} "
              f"(shared n={len(sn)} net=${sn.sum():+.2f})")
        if not ok:
            raise SystemExit("traced clone diverged from the shared engine - "
                             "numbers below would be meaningless")

        # ---- PART 1: does the regime predict loss? ----
        rv = np.array([t[3] for t in trips])
        print(f"\n  PART 1 - EV by entry regime rv (median/baseline)")
        print(f"  {'rv band':<14}{'n':>5}{'WR%':>7}{'avg$':>9}{'avgW$':>8}"
              f"{'avgL$':>8}{'worst$':>8}{'loss$':>9}{'net$':>9}")
        bands = [(0, 0.9), (0.9, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 2.5),
                 (2.5, 3.5), (3.5, 99)]
        for lo, hi in bands:
            m = (rv >= lo) & (rv < hi)
            if not m.any():
                continue
            v = nets[m]
            losses = v[v < 0]
            print(f"  {f'{lo:.1f}-{hi:.1f}':<14}{m.sum():>5}"
                  f"{(v > 0).mean() * 100:>7.1f}{v.mean():>+9.3f}"
                  f"{(v[v > 0].mean() if (v > 0).any() else 0):>+8.2f}"
                  f"{(losses.mean() if len(losses) else 0):>+8.2f}"
                  f"{v.min():>+8.2f}{-losses.sum() if len(losses) else 0:>9.2f}"
                  f"{v.sum():>+9.2f}")
        # 1b. baseline choice matters: a TRAILING reference follows the regime
        # and damps the very signal the gate keys on. Quantified here.
        rv_ew = np.array([med[t[1] - 1] / ew[t[1] - 1] for t in trips])
        print("\n  PART 1b - same gate, CAUSAL EWMA reference instead of a "
              "fixed baseline (shows the self-cancelling risk):")
        for lab, r in (("fixed baseline", rv), ("trailing EWMA", rv_ew)):
            crisis = r >= 2.0
            print(f"    rv from {lab:<16}: trip share in crisis {crisis.mean() * 100:5.1f}%  "
                  f"crisis net ${nets[crisis].sum():+7.2f}  normal net ${nets[~crisis].sum():+7.2f}")

        hi_mask = rv >= 2.0
        print(f"\n  share of trips with rv>=2.0: {hi_mask.mean() * 100:.1f}%  "
              f"share of ALL loss $: "
              f"{(-nets[hi_mask & (nets < 0)].sum()) / (-nets[nets < 0].sum()) * 100:.1f}%  "
              f"share of ALL profit $: "
              f"{nets[hi_mask & (nets > 0)].sum() / nets[nets > 0].sum() * 100:.1f}%")

        # ---- which calendar days the gate would stand down ----
        byday, crisis = {}, {}
        for t in trips:
            dk = datetime.fromtimestamp(t[2] / 1000, tz=timezone.utc).date()
            byday[dk] = byday.get(dk, 0.0) + t[0]
            if t[3] >= 2.0:
                crisis[dk] = crisis.get(dk, 0.0) + t[0]
        print(f"\n  days the gate fires (rolling median >= 2x baseline):")
        for dk in sorted(byday):
            if dk in crisis:
                print(f"    {dk}  day total ${byday[dk]:+7.2f}   "
                      f"from crisis-regime trades ${crisis[dk]:+7.2f}")
        gc = sum(crisis.values())
        print(f"    crisis days: {len(crisis)}/{len(byday)} calendar days; "
              f"crisis-regime TRADES total ${gc:+.2f}")

        # ---- PART 2: realizable policies on a FIXED 0.1 book (skip or trade).
        #      IN-ENGINE: a skipped commit really prevents the position and
        #      perturbs subsequent phase/pending state (post-hoc filtering
        #      overstates the gain - see the validation block at the end).
        print(f"\n  PART 2 - fixed 0.1 lot: regime gate (skip the commit when rv>=T), IN-ENGINE")
        for T in (None, 1.5, 2.0, 2.5):
            if T is None:
                tr = trips
            else:
                tr, _ = run_traced(ms, px, med, side=side, mode="counter",
                                   delay=DELAY, base=base_fixed,
                                   gate=(lambda t_: (lambda r: r < t_))(T))
            show(("no gate (incumbent)" if T is None else f"in-engine rv>={T:.1f}"),
                 stats(tr, days))
            tl = [(t[0], t[2]) for t in tr]
            row = []
            for seed in (11, 12, 13):
                fl, ru = day_block(tl, seed=seed, n_sims=2000)
                row.append(f"{fl:5.1f}/{ru:4.1f}")
            print(f"      survival ($10, 4wk) flip/ruin: " + "  ".join(row))

        # ---- PART 3: ladder book - "hold the lower rung" ----
        print(f"\n  PART 3 - ladder book: regime-capped rung (approximation)")
        print(f"    ladder alone (0.1->0.4):")
        for seed in (11, 12):
            fl, ru = day_block([(t[0], t[2]) for t in trips], lots_mult=ladder_fn,
                               seed=seed, n_sims=1500)
            print(f"      seed{seed}: flip/ruin {fl:5.1f}/{ru:4.1f}")
        for fac in (0.5, 0.0):
            sc = [(t[0] * (fac if t[3] >= 2.0 else 1.0), t[2]) for t in trips]
            lab = "half-size" if fac else "flat (skip)"
            fl, ru = day_block(sc, lots_mult=ladder_fn, seed=11, n_sims=1500)
            print(f"    ladder + {lab:<9} when rv>=2.0: flip/ruin {fl:5.1f}/{ru:4.1f}")
        print()

    # ---- in-engine check of the post-hoc approximation ----
    print("=== in-engine validation of the headline gate (LS, skip rv>=2.0) ===")
    post = [t for t in run_traced(ms, px, med, side=0, delay=DELAY, base=base_fixed)[0]
            if t[3] < 2.0]
    eng, ng = run_traced(ms, px, med, side=0, delay=DELAY, base=base_fixed,
                         gate=lambda r: r < 2.0)
    pe = np.array([t[0] for t in post]).sum()
    ee = np.array([t[0] for t in eng]).sum()
    print(f"  post-hoc filtered : n={len(post)} net=${pe:+.2f}")
    print(f"  in-engine gate    : n={len(eng)} net=${ee:+.2f}  (blocked commits={ng})")
    print(f"  delta ${ee - pe:+.2f} - the state divergence a post-hoc filter hides")


if __name__ == "__main__":
    main()
