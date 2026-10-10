#!/usr/bin/env python3
"""
FORWARD T-C — prospective cluster-skip filter

Oracle ceiling (from THEORY_BATTERY): cluster-start e+1 entries lose on every symbol;
non-cluster-start entries win; gap survives matched-lag controls by +0.3..+1.0.

Forward rule (what you can actually know at decision time):
  At the moment a discharge spike bar i closes, decide whether to enter at i+1.
  Decision variable: count of adverse spikes in [i - W, i) (the trailing window BEFORE the
  current spike). If that count >= K, the current spike is "already in a cluster" from the
  perspective of a trader who has seen the trailing window — skip the entry.
  K in {2, 3}; W in {10, 15, 20} M1 bars (matches the retrospective 10-bar cluster window and
  extends it to test sensitivity).

This is the forward version of: "don't enter on a spike that's the 3rd+ in a cluster."
The oracle version labeled a spike as cluster_start if it was the FIRST of >=3 in 10 bars.
The forward version instead asks: before entering on this spike, have we already seen >=K
adverse spikes in the trailing W bars? If yes, skip.

Control: matched-lag random entries on the SAME resulting entry set (post-filter), so we
measure the filter's value over and above the entry-population effect.
OOS: halves by time.
Reverse-time sanity check: run the same rule on time-reversed data — if the filter's value
disappears when the temporal structure is destroyed, that's evidence it's not a curve-fit
on pure level statistics (the same discipline we applied to the level-touch result).

Baseline to beat: the existing reactive cluster-skip (skip if 3+ in 10 bars AFTER the entry,
i.e. the EA's current cluster logic) is a different thing — it skips subsequent entries after a
cluster is detected. The forward filter is about the entry that STARTS the cluster. So this is
not a replacement for the reactive skip; it's a pre-entry filter that may reduce the number of
cluster-starting entries you take in the first place.
"""
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from partial_m1_corrected import BROKER_FLOOR, half_exit, arrays

DATA = Path("data")
OUT = Path("docs")
SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]

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


def spike_count_trailing(spike_idx, i, window_bars, inclusive=False):
    """Number of discharge spikes in [i - window_bars, i) (exclusive of i by default)."""
    lo = i - window_bars
    if inclusive:
        lo = i - window_bars
    # count spikes in [lo, i) (if inclusive False) or (lo, i] (if inclusive True)
    if inclusive:
        cnt = int(np.searchsorted(spike_idx, i, side="right") -
                  np.searchsorted(spike_idx, lo, side="left"))
    else:
        cnt = int(np.searchsorted(spike_idx, i, side="right") -
                  np.searchsorted(spike_idx, lo, side="right"))
    return cnt


def forward_entry_set(spike_idx, window_bars, k_min, df_len):
    """Return entry indices i+1 for spikes i where trailing count < k_min (i.e. we DO enter)."""
    entries = []
    for i in spike_idx:
        cnt = spike_count_trailing(spike_idx, i, window_bars, inclusive=False)
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


def reverse_time(df):
    """Return a time-reversed copy of df (flip the index order, keep columns)."""
    rev = df.copy()
    rev = rev.iloc[::-1].reset_index(drop=True)
    # time column no longer monotonic after reversal; we only use it as an index proxy for
    # the reverse-time sanity check, so we replace it with the reversed position index.
    rev["time"] = pd.to_datetime(np.arange(len(rev)), unit="s")
    rev["range"] = rev["range"].values
    rev["median_range"] = rev["range"].rolling(100, min_periods=20).median().values
    return rev


def main():
    results = []
    lines = []
    lines.append("# Forward T-C — prospective cluster-skip filter")
    lines.append("")
    lines.append("## Method")
    lines.append("Forward rule: at the close of a discharge spike bar i, enter at i+1 ONLY IF fewer than "
                 "K adverse spikes have occurred in the trailing W M1 bars [i-W, i). "
                 "K in {2, 3}; W in {10, 15, 20}.")
    lines.append("Control: matched-lag random entries on the post-filter entry set (same entry-population effect).")
    lines.append("OOS: halves by time. Reverse-time sanity check on W=10, K=2 (the central case).")
    lines.append("Comparator: full e+1 set (no filter) and the retrospective non-cluster-start subset from the "
                 "oracle test (ceiling).")
    lines.append("Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors, "
                 "2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.")
    lines.append("")

    for sym in SYMS:
        df = load(sym)
        n = len(df)
        spikes_idx = discharges(df, sym)
        print(f"\n=== {sym} ===  spikes={len(spikes_idx)}")

        # Reference: full e+1 population (no filter)
        full_entries = [int(i) + 1 for i in spikes_idx if int(i) + 1 < n - 2]
        full_entries = sorted(set(full_entries))
        full_pnl = run_set(full_entries, df, sym)
        full_avg = float(np.mean(full_pnl)) if full_pnl else 0.0
        full_wr = float((np.array(full_pnl) > 0).mean()) if full_pnl else 0.0
        full_ctrl = matched_lag_control(full_entries, df, max_lag=20, n_per_entry=1, seed=101)
        full_ctrl_pnl = run_set(full_ctrl, df, sym)
        full_ctrl_avg = float(np.mean(full_ctrl_pnl)) if full_ctrl_pnl else 0.0
        print(f"  full e+1: avg={full_avg:.3f} (WR {full_wr:.2f}, n={len(full_pnl)}) ctrl={full_ctrl_avg:.3f} diff={full_avg-full_ctrl_avg:.3f}")

        # Oracle ceiling (retrospective): non-cluster-start subset
        # Recompute the oracle label here for comparison.
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
        cls_start = oracle_label(spikes_idx, window_bars=10)
        oracle_map = {int(spikes_idx[k]): bool(cls_start[k]) for k in range(len(spikes_idx))}
        oracle_non = [e for e in full_entries if not oracle_map.get(e - 1, False)]
        oracle_cls = [e for e in full_entries if oracle_map.get(e - 1, False)]
        oracle_non_pnl = run_set(oracle_non, df, sym)
        oracle_cls_pnl = run_set(oracle_cls, df, sym)
        oracle_non_avg = float(np.mean(oracle_non_pnl)) if oracle_non_pnl else 0.0
        oracle_cls_avg = float(np.mean(oracle_cls_pnl)) if oracle_cls_pnl else 0.0
        oracle_non_ctrl = matched_lag_control(oracle_non, df, max_lag=20, n_per_entry=1, seed=201)
        oracle_non_ctrl_pnl = run_set(oracle_non_ctrl, df, sym)
        oracle_non_ctrl_avg = float(np.mean(oracle_non_ctrl_pnl)) if oracle_non_ctrl_pnl else 0.0
        print(f"  oracle non-cluster-start: avg={oracle_non_avg:.3f} (n={len(oracle_non_pnl)}) ctrl={oracle_non_ctrl_avg:.3f} diff={oracle_non_avg-oracle_non_ctrl_avg:.3f}")
        print(f"  oracle cluster-start: avg={oracle_cls_avg:.3f} (n={len(oracle_cls_pnl)})")

        row = {
            "sym": sym,
            "full_avg": full_avg, "full_ctrl": full_ctrl_avg, "full_diff": full_avg - full_ctrl_avg,
            "oracle_non_avg": oracle_non_avg, "oracle_non_ctrl": oracle_non_ctrl_avg,
            "oracle_non_diff": oracle_non_avg - oracle_non_ctrl_avg,
            "oracle_cls_avg": oracle_cls_avg, "oracle_cls_n": len(oracle_cls_pnl),
            "oracle_non_n": len(oracle_non_pnl),
            "configs": [],
        }

        for W in [10, 15, 20]:
            for K in [2, 3]:
                ent = forward_entry_set(spikes_idx, W, K, n)
                pnls = run_set(ent, df, sym)
                avg = float(np.mean(pnls)) if pnls else 0.0
                wr = float((np.array(pnls) > 0).mean()) if pnls else 0.0
                ctrl = matched_lag_control(ent, df, max_lag=20, n_per_entry=1, seed=301 + W * 10 + K)
                ctrl_pnls = run_set(ctrl, df, sym)
                ctrl_avg = float(np.mean(ctrl_pnls)) if ctrl_pnls else 0.0
                diff = avg - ctrl_avg
                skipped = len(full_entries) - len(ent)
                print(f"  forward W={W} K={K}: avg={avg:.3f} (WR {wr:.2f}, n={len(pnls)}, skipped={skipped}) ctrl={ctrl_avg:.3f} diff={diff:.3f}")
                row["configs"].append({
                    "W": W, "K": K,
                    "avg": avg, "wr": wr, "n": len(pnls), "skipped": skipped,
                    "ctrl": ctrl_avg, "diff": diff,
                })

        # OOS halves for the central case W=10, K=2 (and W=15, K=2 for sensitivity)
        for W, K, tag in [(10, 2, "W10_K2"), (15, 2, "W15_K2")]:
            ent = forward_entry_set(spikes_idx, W, K, n)
            first = int(n * 0.5)
            df1 = df.iloc[:first].reset_index(drop=True)
            df2 = df.iloc[first:].reset_index(drop=True)
            spikes1 = discharges(df1, sym)
            spikes2 = discharges(df2, sym)
            # shift spike indices for df2 into the second-half coordinate space
            ent1 = forward_entry_set(spikes1, W, K, len(df1))
            ent2 = [int(i) + first + 1 for i in spikes2 if int(i) + 1 < len(df2) - 2]
            ent2 = forward_entry_set(np.array([i - first for i in spikes2]), W, K, len(df2))
            ent2 = [int(i) + first for i in ent2]
            pnls1 = run_set(ent1, df1, sym)
            pnls2 = run_set(ent2, df2, sym)
            avg1 = float(np.mean(pnls1)) if pnls1 else 0.0
            avg2 = float(np.mean(pnls2)) if pnls2 else 0.0
            ctrl1 = matched_lag_control(ent1, df1, max_lag=20, n_per_entry=1, seed=401 + W * 10 + K)
            ctrl2 = matched_lag_control(ent2, df2, max_lag=20, n_per_entry=1, seed=402 + W * 10 + K)
            ctrl1_pnls = run_set(ctrl1, df1, sym)
            ctrl2_pnls = run_set(ctrl2, df2, sym)
            ctrl1_avg = float(np.mean(ctrl1_pnls)) if ctrl1_pnls else 0.0
            ctrl2_avg = float(np.mean(ctrl2_pnls)) if ctrl2_pnls else 0.0
            row["configs"].append({
                "W": W, "K": K, "tag": tag, "oos": True,
                "avg1": avg1, "avg2": avg2,
                "ctrl1": ctrl1_avg, "ctrl2": ctrl2_avg,
                "diff1": avg1 - ctrl1_avg, "diff2": avg2 - ctrl2_avg,
                "n1": len(pnls1), "n2": len(pnls2),
            })
            print(f"  OOS {tag}: H1 avg={avg1:.3f} ctrl={ctrl1_avg:.3f} diff={avg1-ctrl1_avg:.3f} | "
                  f"H2 avg={avg2:.3f} ctrl={ctrl2_avg:.3f} diff={avg2-ctrl2_avg:.3f}")

        # Reverse-time sanity check on W=10, K=2
        rdf = reverse_time(df)
        rspikes = discharges(rdf, sym)
        rent = forward_entry_set(rspikes, 10, 2, len(rdf))
        rpnls = run_set(rent, rdf, sym)
        ravg = float(np.mean(rpnls)) if rpnls else 0.0
        rctrl = matched_lag_control(rent, rdf, max_lag=20, n_per_entry=1, seed=501)
        rctrl_pnls = run_set(rctrl, rdf, sym)
        rctrl_avg = float(np.mean(rctrl_pnls)) if rctrl_pnls else 0.0
        row["reverse"] = {"avg": ravg, "ctrl": rctrl_avg, "diff": ravg - rctrl_avg, "n": len(rpnls)}
        print(f"  reverse-time W=10 K=2: avg={ravg:.3f} ctrl={rctrl_avg:.3f} diff={ravg-rctrl_avg:.3f} (sanity)")

        results.append(row)
        lines.append(f"### {sym}")
        lines.append(f"- full e+1: avg={full_avg:.3f} (WR {full_wr:.2f}, n={len(full_pnl)}) ctrl={full_ctrl_avg:.3f} diff={full_avg-full_ctrl_avg:.3f}")
        lines.append(f"- oracle non-cluster-start: avg={oracle_non_avg:.3f} (n={len(oracle_non_pnl)}) ctrl={oracle_non_ctrl_avg:.3f} diff={oracle_non_avg-oracle_non_ctrl_avg:.3f} (ceiling)")
        lines.append(f"- oracle cluster-start: avg={oracle_cls_avg:.3f} (n={len(oracle_cls_pnl)})")
        lines.append("")
        lines.append("| config | avg | WR | n | skipped | ctrl | diff |")
        lines.append("|---|---|---|---|---|---|---|")
        for c in row["configs"]:
            if c.get("oos"):
                lines.append(f"| {c['tag']} OOS H1 | {c['avg1']:.3f} | — | {c['n1']} | — | {c['ctrl1']:.3f} | {c['diff1']:.3f} |")
                lines.append(f"| {c['tag']} OOS H2 | {c['avg2']:.3f} | — | {c['n2']} | — | {c['ctrl2']:.3f} | {c['diff2']:.3f} |")
            else:
                wr = c.get("wr", float((np.array(run_set(forward_entry_set(spikes_idx, c["W"], c["K"], n), df, sym)) > 0).mean()) if False else c.get("wr", 0.0))
                lines.append(f"| W={c['W']} K={c['K']} | {c['avg']:.3f} | {c.get('wr', 0):.2f} | {c['n']} | {c['skipped']} | {c['ctrl']:.3f} | {c['diff']:.3f} |")
        if "reverse" in row:
            rv = row["reverse"]
            lines.append(f"| reverse-time W=10 K=2 | {rv['avg']:.3f} | — | {rv['n']} | — | {rv['ctrl']:.3f} | {rv['diff']:.3f} | (sanity) |")
        lines.append("")

    lines.append("## Interpretation guide")
    lines.append("- The forward filter's value should be judged relative to the oracle non-cluster-start ceiling,")
    lines.append("  not relative to the full e+1 set. The full-set diff is confounded by the entry-population effect")
    lines.append("  (the filter removes cluster-start entries, which are bad for reasons that may or may not be the")
    lines.append("  same as the filter's causal claim). The matched-lag control on the post-filter set is the cleaner")
    lines.append("  comparison: it asks whether the remaining entries are good because of the filter or because of")
    lines.append("  the population they happen to be in.")
    lines.append("- If the forward diff tracks the oracle diff across W/K and both OOS halves are positive and similar,")
    lines.append("  the filter is live-able as a pre-entry rule.")
    lines.append("- If the forward diff collapses toward zero while the oracle diff stays large, the signal is mostly")
    lines.append("  retrospective (you only know it's a cluster after the fact) and the reactive cooldown is the right")
    lines.append("  implementation.")
    lines.append("- The reverse-time diff should be near zero; if it's large, the forward result is likely a")
    lines.append("  level/statistics artifact rather than a temporal one (same discipline as the level-touch audit).")
    lines.append("")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "TC_FORWARD_PART1.md").write_text("\n".join(lines))
    print("\nWrote docs/TC_FORWARD_PART1.md")


if __name__ == "__main__":
    main()
