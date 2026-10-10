#!/usr/bin/env python3
"""
GENESIS AI LAYER — daily run.

  python brain/run_daily.py [--days 7] [--report]

Flow:
  1. learner.learn_all   — regime state per symbol from candles
  2. auditor.audit_all   — what the EA actually did (from trades_*.csv)
  3. recommender.recommend_all — validated config cards (corrected engine,
     train/validation split — nothing leaves here unvalidated)
  4. report.render       — markdown to brain/reports/

Writes per-symbol config cards to SpikeCatcher/brain_config_<key>.json so
a future EA build can read the recommended settings directly.
"""
import os
import sys
import json
import datetime
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import config as cfg_mod  # noqa: E402
from learner import learn_all  # noqa: E402
from auditor import audit_all  # noqa: E402
from recommender import recommend_all  # noqa: E402
import report  # noqa: E402


def write_cards(recs):
    """Write one config card JSON per symbol (what the EA would read)."""
    path_dir = cfg_mod.TRADE_DIR
    os.makedirs(path_dir, exist_ok=True)
    for key, card in recs.items():
        if card.get("verdict") == "ERROR":
            continue
        chosen = card.get("candidate") or card["baseline"]
        payload = {
            "symbol": key,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "verdict": card.get("verdict", "HOLD"),
            "reason": card.get("reason", ""),
            "recommended": chosen,
            "baseline_val_pf": (card.get("baseline_val") or {}).get("pf"),
            "candidate_val_pf": (card.get("candidate_val") or {}).get("pf"),
        }
        with open(os.path.join(path_dir, f"brain_config_{key}.json"), "w") as f:
            json.dump(payload, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="learner window in days")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--cards-only", action="store_true")
    args = ap.parse_args()

    print(f"[brain] daily run @ {datetime.datetime.now().isoformat(timespec='seconds')}")

    recs = recommend_all()
    for key, card in recs.items():
        ver = card.get("verdict")
        mark = {"CHANGE": "->", "HOLD": "ok", "ERROR": "!!"}.get(ver, "?")
        print(f"  {mark} {key:<11} {ver:<7} "
              f"({card.get('reason', '')[:70]})")
    write_cards(recs)

    if args.cards_only:
        return

    learner = learn_all(days=args.days)
    audits = audit_all()
    if not args.no_report:
        path = report.render(learner, audits, recs)
        print(f"[brain] report -> {os.path.relpath(path)}")

    # machine-readable state for other tooling
    state = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "learner": learner,
        "recommendations": {
            k: {kk: vv for kk, vv in v.items() if kk not in ("baseline_train", "candidate_train")}
            for k, v in recs.items()
        },
    }
    os.makedirs(cfg_mod.REPORT_DIR, exist_ok=True)
    with open(os.path.join(cfg_mod.REPORT_DIR, "brain_state.json"), "w") as f:
        json.dump(state, f, default=str, indent=2)


if __name__ == "__main__":
    main()
