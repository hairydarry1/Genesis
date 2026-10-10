#!/usr/bin/env python3
"""Live-account spread quote for Crash Boom Flip 1000 Index (pre-funding gate).

READ-ONLY: opens no orders, changes nothing. Safe to run while the demo
executor is connected (multiple MT5-API readers coexist fine).

What it does:
  1. prints which account the MT5 terminal is logged into (DEMO vs LIVE) -
     this is the whole point: the demo's 0.6261u spread is an ASSUMPTION
     until the live account quotes the same number
  2. samples the live bid/ask 30x over ~30s: mean/median/min/max spread
  3. derives $ per price unit at 0.1 lot from tick_value/tick_size
     (demo reference: 0.10 $/unit -> cross-check)
  4. margin required for one 0.1-lot position + min-lot check
  5. maps the measured spread onto the flip-odds ladder from
     analysis/flip_slippage_review.py (extra cost -> flip/ruin %)

Usage:  python scripts/quote_live_spread.py
"""
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("\\scripts", 1)[0] if "\\scripts" in __file__
                else __file__.rsplit("/scripts", 1)[0])

SYMBOL = "Crash Boom Flip 1000 Index"
LOTS = 0.1
MODEL_SPREAD = 0.05          # u per round trip, what the backtest charged
GROSS_EDGE = 0.235           # $/trade, backtest-exact fills
LADDER_X = [0.000, 0.117, 0.235, 0.352]
LADDER_FLIP = [73.8, 27.3, 3.4, 0.1]
LADDER_RUIN = [22.1, 62.4, 94.4, 99.9]
N_SAMPLES = 30


def main():
    import MetaTrader5 as mt5

    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    try:
        # ---------------- account banner (the safety-critical part) --------
        acct = mt5.account_info()
        if acct is None:
            print("no account info - terminal not logged in?")
            sys.exit(1)
        mode = {0: "DEMO", 1: "CONTEST", 2: "LIVE (REAL MONEY)"}.get(
            acct.trade_mode, f"mode={acct.trade_mode}")
        print("=" * 78)
        print(f"ACCOUNT: {acct.login}  [{mode}]")
        print(f"  server={acct.server}  company={acct.company}")
        print(f"  balance={acct.balance} {acct.currency}  leverage=1:{acct.leverage}")
        if acct.trade_mode != 0:
            print("  >>> THIS IS A LIVE ACCOUNT. Numbers below are REAL-MONEY")
            print("  >>> quotes. Do not fund the $10 until the spread passes.")
        print("=" * 78)

        # ---------------- derive $ per price unit ---------------------------
        info = mt5.symbol_info(SYMBOL)
        if info is None:
            print(f"symbol not available on this account: {SYMBOL}")
            sys.exit(1)
        if info.trade_tick_size and info.trade_tick_value:
            usd_per_unit_per_lot = info.trade_tick_value / info.trade_tick_size
        else:
            usd_per_unit_per_lot = float("nan")
        vpu = usd_per_unit_per_lot * LOTS
        print(f"tick_value={info.trade_tick_value}  tick_size={info.trade_tick_size}"
              f"  ->  ${usd_per_unit_per_lot:.4f}/unit/lot  ->  "
              f"${vpu:.4f}/unit at {LOTS} lot (demo reference: 0.1000)")

        # ---------------- sample the spread ---------------------------------
        print(f"\nsampling {N_SAMPLES} quotes (~{N_SAMPLES}s)...")
        samples = []
        for _ in range(N_SAMPLES):
            t = mt5.symbol_info_tick(SYMBOL)
            if t is not None and t.bid > 0 and t.ask > 0:
                samples.append(t.ask - t.bid)
            time.sleep(1)
        if len(samples) < 5:
            print(f"only {len(samples)} valid quotes - symbol not streaming?")
            sys.exit(1)
        s = np.array(samples)
        print(f"spread  mean {s.mean():.4f}u  median {np.median(s):.4f}u  "
              f"min {s.min():.4f}u  max {s.max():.4f}u  (n={len(s)})")

        # ---------------- margin + min lot ----------------------------------
        mid = (s.mean())
        try:
            margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, SYMBOL, LOTS,
                                           mt5.symbol_info_tick(SYMBOL).ask)
            print(f"margin for {LOTS} lot @ market: {margin:.2f} {acct.currency}"
                  if margin else "margin calc unavailable")
        except Exception as e:
            print(f"margin calc skipped: {e}")
        if info.volume_min > LOTS:
            print(f"[BLOCKER] min lot on this account is {info.volume_min} "
                  f"> planned {LOTS} - sizing model breaks")
        else:
            print(f"min lot {info.volume_min} ok (plan uses {LOTS})")

        # ---------------- the funding math ----------------------------------
        spread_u = float(np.median(s))
        extra = (spread_u - MODEL_SPREAD) * vpu
        flip = float(np.interp(extra, LADDER_X, LADDER_FLIP))
        ruin = float(np.interp(extra, LADDER_X, LADDER_RUIN))
        print("\n" + "=" * 78)
        print(f"SPREAD COST vs backtest model: ${extra:.4f}/round trip "
              f"({spread_u:.4f}u live vs {MODEL_SPREAD}u modeled)")
        print(f"remaining edge: ${GROSS_EDGE - extra:.4f}/trade (gross ${GROSS_EDGE:.3f})")
        print(f"-> 4-week odds at $10 (L+S): flip {flip:.1f}% / ruin {ruin:.1f}%")
        if acct.trade_mode != 0:
            print("\nFUNDING GATE (live account):")
            if spread_u <= 0.63:
                print("  PASS - spread matches demo measurement; deployment plan"
                      " holds at its published odds")
            elif spread_u <= 0.70:
                print("  MARGINAL - spread above demo; re-run the odds ladder"
                      " with this spread before funding")
            else:
                print("  FAIL - live spread materially worse than demo; do not"
                      " fund at 0.1 lot on this edge")
        else:
            print("\n[DEMO account - calibration only. Log the terminal into the")
            print(" live account and re-run for the real gate.]")
        print("=" * 78)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
