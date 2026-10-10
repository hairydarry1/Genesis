#!/usr/bin/env python3
"""
Diagnose two things from tc_forward.py output before writing the verdict:

1. H2 avg == 0.000 on every symbol — is the second-half forward entry set empty, or are the
   PnLs genuinely zero, or is there a reset-index bug in the OOS split? Check counts.

2. Reverse-time diff on crash300 (0.356) and crash1000 (0.057) is POSITIVE, same sign as forward.
   If reverse-time is positive and comparable to forward, the filter may be exploiting a
   level/statistics artifact rather than a temporal one. We check: on time-reversed data, does
   the "trailing window" still correlate with the same spike-subset property that the oracle
   labeled as non-cluster-start? I.e., is the reverse-time forward filter actually reproducing
   the oracle non-cluster-start subset in reverse, which would mean the effect is symmetric and
   thus not temporal?

Also sanity-check the matched-lag control on the forward entry set: is the control's positive avg
(the thing the forward filter goes negative vs) coming from the control drawing entries that are
systematically in the non-cluster-start subset? If the forward filter removes cluster-start entries
and the control re-inserts them randomly, the control should be *better* than the filtered set only
to the extent the filtered set removed bad entries — which is the oracle effect. If the control is
better than the filtered set by MORE than the oracle effect, something is off in the control.
"""
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from partial_m1_corrected import BROKER_FLOOR, half_exit, arrays

DATA = Path("data")
SYMS = ["crash300", "crash1000", "boom500", "boom1000"]

SPIKE_MULT = 3.0


def load(sym):
    df = pd.read_parquet(DATA / f"candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    if "epoch" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
    else:
        df["time"] = pd.to_datetime(df["time"])
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def discharges(df, sym):
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]


def spike_count_trailing(spike_idx, i, window_bars):
    lo = i - window_bars
    return int(np.searchsorted(spike_idx, i, side="right") -
               np.searchsorted(spike_idx, lo, side="right"))


def forward_entry_set(spike_idx, window_bars, k_min, df_len):
    entries = []
    for i in spike_idx:
        cnt = spike_count_trailing(spike_idx, i, window_bars)
        if cnt < k_min:
            e = i + 1
            if 0 < e < df_len - 2:
                entries.append(e)
    return sorted(set(entries))


def trade_at_entry(df, sym, entry_bar, hold=3, partial=True):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, entry_bar + 1, hold, n)
    add_b = False
    if partial:
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, entry_bar + 1, 1, n)
        if rA1 == "timeout":
            fav = (closes[entry_bar + 1] > entry) if is_crash else (closes[entry_bar + 1] < entry)
            add_b = fav
    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, entry_bar + 2, hold - 1, n)
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return pnl


def run_set(entries, df, sym, hold=3, partial=True):
    out = []
    for ei in entries:
        p = trade_at_entry(df, sym, ei, hold=hold, partial=partial)
        if p is not None:
            out.append(p)
    return out


def matched_lag_control(entries, df, max_lag=20, n_per_entry=1, seed=1):
    rng = np.random.RandomState(seed)
    rand = []
    n = len(df)
    entry_set = set(entries)
    for ei in entries:
        for _ in range(n_per_entry):
            cand = ei
            it = 0
            while cand == ei or cand in entry_set or any(abs(cand - e) < 3 for e in entries):
                cand = int(rng.randint(max(0, ei - max_lag), min(n - 1, ei + max_lag + 1)))
                it += 1
                if it > 50:
                    break
            rand.append(cand)
    return rand


def oracle_label(spike_idx, window_bars=10):
    nsp = len(spike_idx)
    cls_start = np.zeros(nsp, dtype=bool)
    if nsp == 0:
        return cls_start
    i = 0
    idxs = spike_idx
    while i < nsp:
        j = i
        while j + 1 < nsp and idxs[j + 1] - idxs[j] <= window_bars:
            j += 1
        if j - i + 1 >= 3:
            cls_start[i] = True
        i = j + 1
    return cls_start


def reverse_time(df):
    rev = df.iloc[::-1].reset_index(drop=True)
    rev["time"] = pd.to_datetime(np.arange(len(rev)), unit="s")
    rev["range"] = rev["range"].values
    rev["median_range"] = rev["range"].rolling(100, min_periods=20).median().values
    return rev


def main():
    for sym in SYMS:
        print(f"\n=== {sym} ===")
        df = load(sym)
        n = len(df)
        spikes_idx = discharges(df, sym)

        # ---- Diagnose H2 == 0 ----
        first = int(n * 0.5)
        df2 = df.iloc[first:].reset_index(drop=True)
        spikes2 = discharges(df2, sym)
        # forward on second half, with spike indices in df2's coordinate space
        ent2 = forward_entry_set(spikes2, 10, 2, len(df2))
        print(f"  H2: n_df2={len(df2)}, spikes_in_H2={len(spikes2)}, forward_entries_H2={len(ent2)}")
        pnls2 = run_set(ent2, df2, sym)
        if len(pnls2) == 0:
            print(f"  H2: entry set empty -> avg 0 is a count artifact, not a real zero-PnL result")
        else:
            arr = np.array(pnls2)
            print(f"  H2: n_pnls={len(pnls2)}, mean={arr.mean():.4f}, min={arr.min():.3f}, max={arr.max():.3f}, "
                  f"fraction_exact_zero={(arr==0).mean():.3f}")
            # Are the entries landing exactly at the boundary where trade_at_entry returns None?
            none_count = 0
            for ei in ent2:
                if ei + 2 >= len(df2):
                    none_count += 1
            print(f"  H2: entries failing entry_bar+2>=len(df2) check: {none_count} of {len(ent2)}")

        # ---- Diagnose reverse-time positive diff ----
        # On reverse-time data, the "trailing window" before a spike becomes the "leading window"
        # in real time. If the forward filter on reverse data selects the same population as the
        # oracle non-cluster-start subset (in reverse coordinates), then the reverse-time effect is
        # just the oracle effect seen backwards, which means it's NOT a temporal asymmetry — it's a
        # set-selection effect that works both ways.
        rdf = reverse_time(df)
        rspikes = discharges(rdf, sym)
        rent = forward_entry_set(rspikes, 10, 2, len(rdf))
        rpnls = run_set(rent, rdf, sym)
        ravg = float(np.mean(rpnls)) if rpnls else 0.0
        rctrl = matched_lag_control(rent, rdf, max_lag=20, n_per_entry=1, seed=501)
        rctrl_pnls = run_set(rctrl, rdf, sym)
        rctrl_avg = float(np.mean(rctrl_pnls)) if rctrl_pnls else 0.0
        print(f"  reverse: forward_entries={len(rent)} (of {len(rspikes)} spikes), avg={ravg:.3f}, ctrl_avg={rctrl_avg:.3f}, diff={ravg-rctrl_avg:.3f}")

        # Map reverse entries back to original coordinates and check oracle label agreement.
        # reverse index r -> original index n-1-r
        oracle_cls = oracle_label(spikes_idx, window_bars=10)
        oracle_map = {int(spikes_idx[k]): bool(oracle_cls[k]) for k in range(len(spikes_idx))}
        # For each reverse forward entry r (which is r_original = n-1 - r in original coords),
        # find the spike that generated it (the spike at r_original-1 in original coords) and check
        # whether it's oracle-non-cluster-start.
        agree = 0
        total = 0
        for r in rent:
            r_orig = n - 1 - r
            spike_orig = r_orig - 1
            if spike_orig in oracle_map:
                total += 1
                if not oracle_map[spike_orig]:  # non-cluster-start in original
                    agree += 1
        frac_noncluster = agree / total if total else float("nan")
        print(f"  reverse: of {total} reverse-forward entries mappable to an original spike, "
              f"{agree} ({frac_noncluster:.3f}) are oracle non-cluster-start in original coords")

        # Compare: on forward (real-time) data with W=10 K=2, what fraction of the selected entries
        # are oracle non-cluster-start?
        fwd_ent = forward_entry_set(spikes_idx, 10, 2, n)
        fwd_total = 0
        fwd_agree = 0
        for e in fwd_ent:
            spike_orig = e - 1
            if spike_orig in oracle_map:
                fwd_total += 1
                if not oracle_map[spike_orig]:
                    fwd_agree += 1
        fwd_frac = fwd_agree / fwd_total if fwd_total else float("nan")
        print(f"  forward W=10 K=2: of {fwd_total} selected entries, {fwd_agree} ({fwd_frac:.3f}) are oracle non-cluster-start")

        # ---- Control composition check ----
        # Is the matched-lag control on the forward entry set re-introducing cluster-start entries?
        ctrl = matched_lag_control(fwd_ent, df, max_lag=20, n_per_entry=1, seed=501)
        ctrl_total = 0
        ctrl_agree = 0
        for e in ctrl:
            spike_orig = e - 1
            if spike_orig in oracle_map:
                ctrl_total += 1
                if not oracle_map[spike_orig]:
                    ctrl_agree += 1
        ctrl_frac = ctrl_agree / ctrl_total if ctrl_total else float("nan")
        print(f"  control on forward set: of {ctrl_total} control entries mappable, {ctrl_agree} ({ctrl_frac:.3f}) are oracle non-cluster-start")
        ctrl_pnls = run_set(ctrl, df, sym)
        print(f"  control avg={float(np.mean(ctrl_pnls)) if ctrl_pnls else 0:.3f} (n={len(ctrl_pnls)})")

        # Expected: if the filter is purely selecting the oracle-non-cluster-start subset, then
        # forward-frac should be high, control-frac should be lower (control reintroduces some
        # cluster-start entries), and control-avg should sit between forward-avg and oracle-non-avg.
        # If forward-frac is NOT high (forward filter isn't actually selecting the oracle subset),
        # then the forward filter is a different population and the negative diff is real.


if __name__ == "__main__":
    main()
