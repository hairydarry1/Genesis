"""
eval/backtest_profit_targets.py

Backtest how profit target tiers affect max drawdown and total return
over 30 days of data for all BOOM/CRASH variants.

Optimized: uses vectorized data loading and processes only the last N days.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from eval.paper_trading_v2 import SpikeFadeEngine, SPIKE_FADE_CONFIGS, INITIAL_EQUITY


def load_data(symbol: str, days: int = 30) -> pd.DataFrame:
    """Load parquet candle data."""
    name_map = {
        "BOOM500": "candles_boom500_1min.parquet",
        "CRASH500": "candles_crash500_1min.parquet",
        "BOOM1000": "candles_boom1000_1min.parquet",
        "CRASH1000": "candles_crash1000_1min.parquet",
    }
    path = os.path.join("data", name_map[symbol])
    df = pd.read_parquet(path)
    
    # Take last N days
    bars_needed = days * 24 * 60
    if len(df) > bars_needed:
        df = df.iloc[-bars_needed:]
    
    return df


def run_backtest(symbol: str, days: int, risk_pct: float, 
                 profit_tiers=None, label: str = "") -> dict:
    """Run a single backtest and return results."""
    df = load_data(symbol, days)
    
    config = SPIKE_FADE_CONFIGS.get(symbol)
    engine = SpikeFadeEngine(symbol, config=config, risk_pct=risk_pct)
    if profit_tiers:
        engine.set_profit_tiers(profit_tiers)
    
    trades = []
    tier_events = []
    last_tier_count = 0
    
    for _, row in df.iterrows():
        candle = {
            "epoch": int(row.get("epoch", 0)),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
        }
        
        result = engine.on_bar(candle)
        
        if result and result.get("action") == "close":
            trades.append(result)
        
        # Track tier transitions
        if len(engine._tier_log) > last_tier_count:
            tier_events.append(engine._tier_log[-1])
            last_tier_count = len(engine._tier_log)
    
    # Final close if still in position
    if engine.position != 0 and len(df) > 0:
        last_price = float(df.iloc[-1]["close"])
        result = engine._close_position(last_price, "backtest_end", last_price)
        trades.append(result)
    
    # Compute stats
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    final_eq = engine.equity
    
    # Max drawdown from equity curve
    equity_arr = [INITIAL_EQUITY]
    for t in trades:
        equity_arr.append(t.get("equity", equity_arr[-1]))
    equity_arr = np.array(equity_arr)
    peaks = np.maximum.accumulate(equity_arr)
    dds = (peaks - equity_arr) / peaks
    max_dd = float(np.max(dds)) if len(dds) > 0 else 0
    
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
    total_return = (final_eq / INITIAL_EQUITY - 1) * 100
    risk_adj = total_return / max_dd if max_dd > 0 else total_return * 100
    
    return {
        "label": label,
        "trades": len(trades),
        "t_per_day": len(trades) / days if days > 0 else 0,
        "return_pct": total_return,
        "final_eq": final_eq,
        "wr": win_rate,
        "pf": profit_factor,
        "max_dd": max_dd * 100,
        "risk_adj": risk_adj,
        "risk_now": engine.risk_pct,
        "paused": engine._trading_paused,
        "tiers": tier_events,
    }


def print_table(results: list[dict], title: str):
    """Print formatted comparison."""
    print(f"\n{'='*78}")
    print(f"  {title}")
    print(f"{'='*78}")
    print(f"  {'Config':<25} {'Trades':>6} {'T/day':>5} {'Return':>8} {'WR':>5} {'PF':>5} {'MaxDD':>6} {'RskAdj':>7} {'Equity':>10}")
    print(f"  {'-'*74}")
    
    best_ra = 0
    best_idx = 0
    for i, r in enumerate(results):
        paused = " STOP" if r["paused"] else ""
        print(f"  {r['label']:<25} {r['trades']:>6} {r['t_per_day']:>5.1f} "
              f"{r['return_pct']:>+7.2f}% {r['wr']:>4.0f}% "
              f"{r['pf']:>5.2f} {r['max_dd']:>5.2f}% "
              f"{r['risk_adj']:>7.1f} ${r['final_eq']:>9,.2f}{paused}")
        if r["risk_adj"] > best_ra:
            best_ra = r["risk_adj"]
            best_idx = i
    
    print(f"  {'-'*74}")
    print(f"  >>> Best risk-adjusted: {results[best_idx]['label']} (RskAdj={best_ra:.1f})")
    
    # Tier events
    for r in results:
        if r["tiers"]:
            events_str = " -> ".join(
                f"+{e['return_pct']:.0f}%({e['action']})" for e in r["tiers"]
            )
            print(f"  {r['label']}: {events_str}")
    print()


def main():
    symbols = ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]
    days = 30
    risk = 0.10
    
    # Configs: (label, tiers or None)
    configs = [
        ("No targets", None),
        ("Conservative", [(5, 0.5), (10, 0.25), (15, 0.1), (20, 0)]),
        ("Moderate", [(10, 0.5), (20, 0.25), (30, 0)]),
        ("Aggressive", [(15, 0.5), (25, 0.25), (35, 0)]),
        ("Easy stop", [(8, 0.5), (15, 0.25), (20, 0)]),
        ("Lock & walk", [(10, 0.5), (15, 0.1), (20, 0)]),
    ]
    
    all_best = {}
    
    for symbol in symbols:
        print(f"\n  Loading {symbol}...")
        try:
            df = load_data(symbol, days)
        except Exception as e:
            print(f"  SKIPPED {symbol}: {e}")
            continue
        
        actual_days = len(df) / (24 * 60)
        print(f"  {symbol}: {len(df)} bars ({actual_days:.1f} days)")
        
        results = []
        for label, tiers in configs:
            r = run_backtest(symbol, days, risk, profit_tiers=tiers, label=label)
            results.append(r)
        
        print_table(results, f"{symbol} — {days} Days, {risk*100:.0f}% Base Risk")
        
        # Track best per symbol
        best = max(results, key=lambda x: x["risk_adj"])
        all_best[symbol] = best
    
    # Final summary
    print(f"\n{'#'*78}")
    print(f"  OPTIMAL CONFIG PER SYMBOL")
    print(f"{'#'*78}\n")
    
    for sym, b in all_best.items():
        print(f"  {sym}: {b['label']}")
        print(f"    Return: {b['return_pct']:+.2f}%  |  MaxDD: {b['max_dd']:.2f}%  |  "
              f"RiskAdj: {b['risk_adj']:.1f}  |  WR: {b['wr']:.0f}%  |  PF: {b['pf']:.2f}")
        if b["tiers"]:
            events = " -> ".join(f"+{e['return_pct']:.0f}%→{e['action']}" for e in b["tiers"])
            print(f"    Tiers: {events}")
        print()


if __name__ == "__main__":
    main()
