#!/usr/bin/env python3
"""
BRAIN RECOMMENDER — proposes per-symbol EA configs, validated.

Discipline (the checks-and-balances rule from the playbook):
  1. Candidates are small perturbations of the VERIFIED baseline.
  2. Candidate selection uses the TRAIN split (older 60%).
  3. Nothing is recommended unless it also passes the VALIDATION split
     (newer 40% — the part closest to what will happen next):
         PF_val >= 1.15
         AND PF_val >= baseline PF_val - 0.10
  4. If nothing passes, the baseline is kept ("HOLD").
Gate is only ever proposed on 300/500 symbols (never the 1000s).
"""
import numpy as np

from config import by_key, GATE_FORBIDDEN_KEYS
from engine import load_prep, run_cfg, outcome_stats, stats


def _baseline(key):
    return dict(by_key(key)["verified"])


def _candidates(key, base):
    holds = sorted({max(2, base["hold"] + d) for d in (-1, 0, 1)})
    cds = sorted({1, base.get("cooldown", 1)} | {base.get("cooldown", 1)})
    gate_vals = [False]
    if key not in GATE_FORBIDDEN_KEYS:
        gate_vals = [False, True] if not base.get("gate") else [True, False]
    # entry-bar candidates: 1 always, plus the verified bar and one beyond
    ebs = sorted({1, base.get("entry_bar", 1), min(3, base.get("entry_bar", 1) + 1)})
    out = []
    for h in holds:
        for cd in cds:
            for g in gate_vals:
                for eb in ebs:
                    c = {"entry_bar": eb, "hold": h, "cooldown": cd,
                         "partial": True, "gate": g,
                         "gate_mult": 8.0, "gate_window": 3}
                    if (eb == base.get("entry_bar", 1) and h == base["hold"] and
                            cd == base.get("cooldown", 1) and
                            g == base.get("gate", False)):
                        continue
                    out.append(c)
    return out


def _eval(sym, cfg, df):
    tr = run_cfg(sym, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                 gate=cfg["gate"], gate_mult=cfg.get("gate_mult", 8.0),
                 gate_window=cfg.get("gate_window", 3), df=df,
                 entry_bar=cfg.get("entry_bar", 1))
    return outcome_stats(tr)


def recommend_symbol(key, train_frac=0.6, min_trades=20):
    base = _baseline(key)
    df = load_prep(key)
    n = len(df)
    cut = int(n * train_frac)
    dft = df.iloc[:cut].reset_index(drop=True)
    dfv = df.iloc[cut:].reset_index(drop=True)

    base_tr = _eval(key, base, dft)
    base_va = _eval(key, base, dfv)

    card = {
        "key": key,
        "baseline": base,
        "baseline_train": base_tr,
        "baseline_val": base_va,
        "candidate": None,
        "candidate_train": None,
        "candidate_val": None,
        "verdict": "HOLD",
        "reason": "no candidate beat the baseline on validation",
    }

    if base_va is None or base_va["n"] < min_trades:
        card["reason"] = f"validation sample too small (n={0 if base_va is None else base_va['n']})"
        return card

    best = None
    for c in _candidates(key, base):
        tr = _eval(key, c, dft)
        if tr is None or tr["n"] < min_trades:
            continue
        va = _eval(key, c, dfv)
        if va is None or va["n"] < min_trades:
            continue
        # selection on train quality, gated on validation pass
        score = (tr["pf"], tr["avg"])
        if best is None or score[0] > best[0][0]:
            best = (score, c, tr, va)

    if best is None:
        card["reason"] = "no candidate had enough trades on both splits"
        return card

    _, c, tr, va = best
    base_pf_va = base_va["pf"] if base_va else 0.0
    ok = (va["pf"] >= 1.15) and (va["pf"] >= base_pf_va - 0.10)
    if ok and (va["pf"] > base_pf_va + 0.02 or tr["pf"] > base_tr["pf"] + 0.05):
        card.update({"candidate": c, "candidate_train": tr, "candidate_val": va,
                     "verdict": "CHANGE", "reason": "candidate passed validation"})
    return card


def recommend_all(train_frac=0.6):
    from config import SYMBOLS
    out = {}
    for s in SYMBOLS:
        try:
            out[s["key"]] = recommend_symbol(s["key"], train_frac=train_frac)
        except Exception as e:  # never crash the daily run on one symbol
            out[s["key"]] = {"key": s["key"], "verdict": "ERROR", "reason": str(e)}
    return out


if __name__ == "__main__":
    import json
    for k, c in recommend_all().items():
        print(json.dumps({k2: v2 for k2, v2 in c.items()}, default=str, indent=1)[:1600])
        print("-" * 40)
