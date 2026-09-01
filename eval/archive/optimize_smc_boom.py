"""
eval/optimize_smc_boom.py — Deep optimization of SMC Spike Hunter for BOOM symbols.

BOOM characteristics:
- Spikes go UP (we BUY the fade = LONG in demand zones)
- SMC enters at H1 demand zones with RSI oversold + EMA bullish cross
- $0.20/point for BOOM500, $0.10/point for BOOM1000
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
import time
import json

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks,
    compute_rsi, compute_ema, compute_sma, price_in_zone, OrderBlock
)


# ─── SMC Backtest Engine ──────────────────────────────────────
def run_smc_backtest(
    symbol: str,
    m1_candles: list,
    h1_candles: list,
    rsi_period: int = 14,
    rsi_extreme_low: float = 10.0,
    ema_fast: int = 9,
    ema_slow: int = 18,
    sl_pct: float = 0.005,
    risk: float = 0.01,
    max_hold: int = 60,
    zone_mult: float = 2.5,
    entry_slippage: float = 0.00011,
    max_trades_per_session: int = 5,
    spike_exit_count: int = 2,
) -> dict:
    """Run SMC backtest with custom parameters."""
    zones = detect_order_blocks(h1_candles, threshold_mult=zone_mult)

    cfg = SMCHunterConfig(
        rsi_period=rsi_period,
        rsi_extreme_low=rsi_extreme_low,
        rsi_extreme_high=100 - rsi_extreme_low,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        sl_pct=sl_pct,
        risk_per_trade=risk,
        max_hold=max_hold,
        max_trades_per_session=max_trades_per_session,
        spike_exit_count=spike_exit_count,
        entry_slippage_pct=entry_slippage,
        use_scaling=False,
    )

    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1_candles)

    for i, bar in enumerate(m1_candles):
        smc.on_bar(bar, i)

    if smc.position != 0:
        smc._close_position(m1_candles[-1]["close"], "eod", m1_candles[-1]["close"])

    trades = smc.trade_log
    n = len(trades)

    if n == 0:
        return {
            "trades": 0, "zones": len(zones),
            "signals": dict(smc._signals_seen),
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    tw = sum(wins) if wins else 0
    tl = abs(sum(losses)) if losses else 0

    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100

    # Exit breakdown
    ex = {}
    for t in trades:
        r = t["exit_reason"]
        if r not in ex:
            ex[r] = {"count": 0, "pnl": 0, "wins": 0}
        ex[r]["count"] += 1
        ex[r]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            ex[r]["wins"] += 1

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "total_pnl": sum(pnls),
        "profit_factor": tw / tl if tl > 0 else 99,
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "max_dd": dd.max(),
        "avg_rr": np.mean(wins) / abs(np.mean(losses)) if losses and np.mean(losses) != 0 else 99,
        "exit_reasons": ex,
        "zones": len(zones),
        "signals": dict(smc._signals_seen),
        "config": {
            "rsi_period": rsi_period,
            "rsi_low": rsi_extreme_low,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "sl_pct": sl_pct,
            "max_hold": max_hold,
            "zone_mult": zone_mult,
        },
    }


# ─── Grid Search ───────────────────────────────────────────────
def grid_search_smc(symbol: str, m1_candles: list, h1_candles: list, dpp: float):
    """Grid search over SMC parameters."""
    print(f"\n  GRID SEARCH: Testing SMC parameter combinations for {symbol}...")
    print(f"  {'Config':55s} {'T':>4s} {'WR%':>5s} {'PF':>5s} {'PnL$':>9s} {'DD%':>5s} {'Score':>6s}")
    print(f"  {'-'*55} {'-'*4} {'-'*5} {'-'*5} {'-'*9} {'-'*5} {'-'*6}")

    best_score = -999
    best_params = None
    best_result = None
    all_results = []

    total = 0
    # Pre-defined promising configs based on research
    configs = [
        # (rsi_period, rsi_low, ema_fast, ema_slow, sl_pct, max_hold, zone_mult, label)
        (14, 10, 9, 18, 0.005, 60, 2.5, "Baseline"),
        (14, 15, 9, 18, 0.005, 60, 2.5, "Relaxed RSI"),
        (14, 10, 7, 18, 0.005, 60, 2.5, "Fast EMA"),
        (14, 10, 9, 18, 0.004, 60, 2.5, "Tighter SL"),
        (14, 10, 9, 18, 0.008, 60, 2.5, "Wider SL"),
        (14, 10, 9, 18, 0.005, 45, 2.5, "Faster exit"),
        (14, 10, 9, 18, 0.005, 90, 2.5, "Patient"),
        (14, 10, 9, 18, 0.005, 60, 2.0, "Tight zones"),
        (14, 10, 9, 18, 0.005, 60, 3.0, "Wide zones"),
        (14, 20, 9, 18, 0.005, 60, 2.5, "Very relaxed RSI"),
        (14, 8, 9, 18, 0.005, 60, 2.5, "Strict RSI"),
        (14, 10, 9, 18, 0.006, 60, 2.5, "Medium SL"),
        (14, 15, 7, 18, 0.005, 60, 2.5, "Relaxed+Fast"),
        (14, 10, 9, 18, 0.005, 30, 2.5, "Quick exit"),
        (14, 10, 9, 21, 0.005, 60, 2.5, "Slow EMA"),
    ]

    for cfg in configs:
        rsi_p, rsi_low, ema_f, ema_s, sl, hold, zm, label = cfg
        if ema_f >= ema_s:
            continue
        total += 1
        r = run_smc_backtest(
            symbol, m1_candles, h1_candles,
            rsi_period=rsi_p,
            rsi_extreme_low=rsi_low,
            ema_fast=ema_f,
            ema_slow=ema_s,
            sl_pct=sl,
            max_hold=hold,
            zone_mult=zm,
        )
        if r["trades"] < 3:
            continue

        # Score
        score = (
            r["profit_factor"]
            * (r["win_rate"] / 100)
            * np.log1p(r["trades"])
            * (1 - min(r["max_dd"], 100) / 100)
        )

        params = r["config"]
        params["label"] = label
        all_results.append((score, r, params))

        if score > best_score:
            best_score = score
            best_result = r
            best_params = params

    all_results.sort(key=lambda x: x[0], reverse=True)

    # Print top 20
    for i, (score, r, params) in enumerate(all_results[:20]):
        config_str = (f"RSI={params['rsi_period']:2d}/{params['rsi_low']:2.0f} "
                     f"EMA={params['ema_fast']:2d}/{params['ema_slow']:2d} "
                     f"SL={params['sl_pct']*100:.1f}% "
                     f"h={params['max_hold']:3d} "
                     f"ZM={params['zone_mult']:.1f}")
        print(f"  {config_str:55s} {r['trades']:4d} {r['win_rate']:5.1f} {r['profit_factor']:5.2f} "
              f"${r['total_pnl']:>+8.2f} {r['max_dd']:5.1f} {score:6.2f}")

    print(f"\n  Tested {total} combinations, {len(all_results)} had >= 10 trades")

    return best_params, best_result, all_results


# ─── Compounding Projection ────────────────────────────────────
def project_compounding(result, dpp, risk_pct=0.02):
    """Project returns with compounding."""
    if result["trades"] == 0:
        return

    trades_per_day = result["trades"] / 30
    avg_pnl = result["total_pnl"] / result["trades"]

    print(f"\n  COMPUNDING PROJECTION ({risk_pct*100:.0f}% risk per trade):")
    print(f"  {'Equity':>8s} {'Risk/Trade':>10s} {'Monthly':>10s} {'Annual':>12s}")
    print(f"  {'-'*40}")

    for equity in [10, 20, 50, 100, 200, 500]:
        risk_dollars = equity * risk_pct
        # Min loss based on SL
        sl_pct = result["config"]["sl_pct"]
        min_loss = equity * sl_pct if sl_pct > 0 else 1
        units = risk_dollars / min_loss if min_loss > 0 else 0

        monthly = trades_per_day * 30 * avg_pnl * units
        annual = monthly * 12

        print(f"  ${equity:>6d}  ${risk_dollars:>8.2f}  ${monthly:>+9.2f}  ${annual:>+11.2f}")


# ─── Main ──────────────────────────────────────────────────────
def main():
    print(f"\n{'='*72}")
    print(f"  SMC SPIKE HUNTER OPTIMIZATION — BOOM SYMBOLS")
    print(f"  30-day M1 data")
    print(f"{'='*72}")

    symbols = {
        "BOOM500": {"mt5": "boom500", "dpp": 0.20},
        "BOOM1000": {"mt5": "boom1000", "dpp": 0.10},
    }

    all_best = {}

    for symbol, cfg in symbols.items():
        mt5_name = cfg["mt5"]
        dpp = cfg["dpp"]

        print(f"\n{'='*72}")
        print(f"  {symbol} (${dpp}/point)")
        print(f"{'='*72}")

        # Load data
        try:
            m1 = load_candles(mt5_name, "1min")
            h1 = load_candles(mt5_name, "60min")
        except FileNotFoundError as e:
            print(f"  Data not found: {e}")
            continue

        closes = np.array([b["close"] for b in m1], dtype=float)
        print(f"  M1 bars: {len(m1)}, H1 bars: {len(h1)}")
        print(f"  Price: {closes[-1]:.4f}, Range: {closes.min():.4f}-{closes.max():.4f}")

        # Grid search
        t0 = time.time()
        best_params, best_result, all_results = grid_search_smc(symbol, m1, h1, dpp)
        elapsed = time.time() - t0
        print(f"\n  Grid search took {elapsed:.1f}s")

        if best_params and best_result["trades"] > 0:
            print(f"\n  {'='*72}")
            print(f"  BEST CONFIGURATION: {symbol}")
            print(f"  {'='*72}")
            print(f"  RSI period:       {best_params['rsi_period']}")
            print(f"  RSI extreme low:  {best_params['rsi_low']}")
            print(f"  EMA fast:         {best_params['ema_fast']}")
            print(f"  EMA slow:         {best_params['ema_slow']}")
            print(f"  SL percentage:    {best_params['sl_pct']*100:.1f}%")
            print(f"  Max hold:         {best_params['max_hold']} bars")
            print(f"  Zone multiplier:  {best_params['zone_mult']}")

            print(f"\n  Performance:")
            print(f"    Trades:         {best_result['trades']}")
            print(f"    Win Rate:       {best_result['win_rate']:.1f}%")
            print(f"    Profit Factor:  {best_result['profit_factor']:.2f}")
            print(f"    Total PnL:      ${best_result['total_pnl']:+.2f}")
            print(f"    Max Drawdown:   {best_result['max_dd']:.2f}%")
            print(f"    Avg Win:        ${best_result['avg_win']:+.2f}")
            print(f"    Avg Loss:       ${best_result['avg_loss']:+.2f}")
            print(f"    Avg R:R:        1:{best_result['avg_rr']:.1f}")
            print(f"    H1 Zones:       {best_result['zones']}")

            print(f"\n  Exit Reasons:")
            for reason, stats in sorted(best_result["exit_reasons"].items()):
                wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
                print(f"    {reason:15s}: {stats['count']:3d} trades, PnL=${stats['pnl']:+.2f}, WR={wr:.0f}%")

            print(f"\n  Signal Funnel:")
            sig = best_result["signals"]
            print(f"    Zone hits:    {sig.get('zone', 0)}")
            print(f"    RSI signals:  {sig.get('rsi', 0)}")
            print(f"    EMA crosses:  {sig.get('ema', 0)}")
            print(f"    Full (entry): {sig.get('full', 0)}")

            # Compounding
            print(f"\n  {'='*72}")
            print(f"  COMPOUNDING PROJECTIONS: {symbol}")
            print(f"  {'='*72}")
            project_compounding(best_result, dpp, risk_pct=0.01)
            project_compounding(best_result, dpp, risk_pct=0.02)

            all_best[symbol] = {
                "params": best_params,
                "result": {k: v for k, v in best_result.items() if k != "exit_reasons"},
                "exit_reasons": best_result.get("exit_reasons", {}),
                "top_10": [
                    {"params": p, "trades": r["trades"], "wr": r["win_rate"],
                     "pf": r["profit_factor"], "pnl": r["total_pnl"], "dd": r["max_dd"]}
                    for s, r, p in all_results[:10]
                ],
            }

    # Combined portfolio
    if all_best:
        print(f"\n{'='*72}")
        print(f"  COMBINED PORTFOLIO PROJECTION")
        print(f"{'='*72}")

        for risk_pct in [0.01, 0.02]:
            print(f"\n  Risk: {risk_pct*100:.0f}% per trade")
            for equity in [10, 50, 100]:
                total_monthly = 0
                for sym, data in all_best.items():
                    r = data["result"]
                    dpp = symbols[sym]["dpp"]
                    if r["trades"] > 0:
                        trades_per_day = r["trades"] / 30
                        avg_pnl = r["total_pnl"] / r["trades"]
                        sl_pct = data["params"]["sl_pct"]
                        risk_dollars = equity * risk_pct
                        min_loss = equity * sl_pct
                        units = risk_dollars / min_loss if min_loss > 0 else 0
                        monthly = trades_per_day * 30 * avg_pnl * units
                        total_monthly += monthly

                print(f"    ${equity:>4d} equity: ${total_monthly:>+8.2f}/month (${total_monthly*12:>+9.2f}/year)")

    # Save results
    output_path = Path("checkpoints_tiny")
    output_path.mkdir(exist_ok=True)
    with open(output_path / "smc_boom_optimized.json", "w") as f:
        json.dump(all_best, f, indent=2, default=str)
    print(f"\n  Saved to {output_path}/smc_boom_optimized.json")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    main()
