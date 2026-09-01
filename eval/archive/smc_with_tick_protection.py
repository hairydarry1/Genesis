"""
eval/smc_with_tick_protection.py — SMC + Tick Count Exit Protection for BOOM

Architecture:
  ENTRY: SMC (zone + RSI + EMA crossover)
  EXIT PROTECTION: If bars since spike > threshold, EXIT early (spike imminent)

For BOOM symbols:
  - Spikes go UP (against our LONG position)
  - When bars_since_spike is HIGH, next spike UP is imminent
  - EXIT LONG before spike hits to protect profits
"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks,
    compute_rsi, compute_ema, compute_sma, price_in_zone, OrderBlock
)


def detect_m1_spikes(closes: np.ndarray, threshold: float = 3.0) -> np.ndarray:
    """Detect spikes by bar range vs median."""
    n = len(closes)
    ranges = np.zeros(n)
    for i in range(1, n):
        ranges[i] = abs(closes[i] - closes[i-1])
    ranges[0] = ranges[1] if n > 1 else 0
    med = np.median(ranges[ranges > 0])
    return np.where(ranges > med * threshold)[0]


def run_smc_with_protection(
    symbol: str,
    m1_candles: list,
    h1_candles: list,
    sl_pct: float = 0.005,
    risk: float = 0.01,
    rsi_low: float = 15.0,
    rsi_high: float = 85.0,
    max_hold: int = 60,
    zone_mult: float = 2.5,
    # TICK COUNT PROTECTION
    use_tick_protection: bool = True,
    tick_threshold: int = 15,
) -> dict:
    """Run SMC backtest with tick count exit protection."""
    is_boom = "boom" in symbol.lower()
    
    # Detect spikes for tick counting
    closes = np.array([b["close"] for b in m1_candles], dtype=float)
    spikes = detect_m1_spikes(closes)
    spike_set = set(spikes.tolist())
    
    # Initialize SMC
    cfg = SMCHunterConfig(
        sl_pct=sl_pct, risk_per_trade=risk,
        rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high,
        max_hold=max_hold, entry_slippage_pct=0.00011, use_scaling=False,
    )
    
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    zones = detect_order_blocks(h1_candles, threshold_mult=zone_mult)
    smc.set_h1_zones(zones)
    smc.precompute(m1_candles)
    
    # Track bars since spike for each bar
    last_spike = -999
    bars_since_spike = np.zeros(len(m1_candles), dtype=int)
    for i in range(len(m1_candles)):
        if (i - 1) in spike_set:
            last_spike = i - 1
        bars_since_spike[i] = i - last_spike if last_spike >= 0 else 999
    
    # Run backtest with custom exit logic
    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    trade_pnls = []
    trade_reasons = []
    
    for i, bar in enumerate(m1_candles):
        h, l, c = bar["high"], bar["low"], bar["close"]
        dpp = 0.20 if "500" in symbol else 0.10
        
        # Check exits first
        if in_trade:
            bars_held = i - entry_idx
            
            # Default exit checks (same as SMC)
            hit_tp = False
            hit_sl = False
            exit_reason = None
            
            if entry_price > 0:
                if is_boom:  # LONG
                    hit_tp = (h - entry_price) >= entry_price * sl_pct
                    hit_sl = (l - entry_price) <= -entry_price * sl_pct
                    unrealized = c - entry_price
                else:  # SHORT
                    hit_tp = (entry_price - l) >= entry_price * sl_pct
                    hit_sl = (h - entry_price) <= entry_price * sl_pct
                    unrealized = entry_price - c
                
                if hit_tp:
                    exit_reason = "tp"
                    trade_pnls.append(entry_price * sl_pct * dpp)
                elif hit_sl:
                    exit_reason = "sl"
                    trade_pnls.append(-entry_price * sl_pct * dpp)
                elif bars_held >= max_hold:
                    exit_reason = "timeout"
                    trade_pnls.append(unrealized * dpp)
                
                # TICK COUNT PROTECTION
                elif use_tick_protection and bars_since_spike[i] >= tick_threshold:
                    exit_reason = "tick_protection"
                    trade_pnls.append(unrealized * dpp)
                
                # Spike against us
                elif i in spike_set and bars_held > 2:
                    exit_reason = "spike_against"
                    trade_pnls.append(unrealized * dpp)
                
                if exit_reason:
                    trade_reasons.append(exit_reason)
                    in_trade = False
        
        # Check entry
        if not in_trade:
            result = smc.on_bar(bar, i)
            if result and result.get("action") == "open":
                in_trade = True
                entry_price = result["entry_price"]
                entry_idx = i
    
    # Close any open position
    if in_trade:
        c = m1_candles[-1]["close"]
        dpp = 0.20 if "500" in symbol else 0.10
        if is_boom:
            trade_pnls.append((c - entry_price) * dpp)
        else:
            trade_pnls.append((entry_price - c) * dpp)
        trade_reasons.append("eod")
    
    return _summarize(trade_pnls, trade_reasons)


def _summarize(pnls, reasons):
    """Compute metrics."""
    if not pnls:
        return {"trades": 0}
    
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    tw = sum(wins) if wins else 0
    tl = abs(sum(losses)) if losses else 0
    
    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100
    
    reason_stats = {}
    for r, p in zip(reasons, pnls):
        if r not in reason_stats:
            reason_stats[r] = {"count": 0, "pnl": 0, "wins": 0}
        reason_stats[r]["count"] += 1
        reason_stats[r]["pnl"] += p
        if p > 0:
            reason_stats[r]["wins"] += 1
    
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
        "exit_reasons": reason_stats,
    }


def main():
    print(f"\n{'='*72}")
    print(f"  SMC + TICK COUNT EXIT PROTECTION — BOOM SYMBOLS")
    print(f"  30-day M1 data from MT5")
    print(f"{'='*72}")
    
    symbols = {
        "BOOM500": {"mt5": "boom500", "dpp": 0.20, "zone_mult": 2.5},
        "BOOM1000": {"mt5": "boom1000", "dpp": 0.10, "zone_mult": 2.5},
    }
    
    for symbol, cfg in symbols.items():
        mt5_name = cfg["mt5"]
        dpp = cfg["dpp"]
        
        # Load data
        m1 = load_candles(mt5_name, "1min")
        h1 = load_candles(mt5_name, "60min")
        
        closes = np.array([b["close"] for b in m1], dtype=float)
        print(f"\n  {'='*70}")
        print(f"  {symbol} ({dpp}/point, {len(m1)} bars)")
        print(f"  {'='*70}")
        
        # ── Baseline: No protection ──
        print(f"\n  [1] BASELINE (no tick protection):")
        r_base = run_smc_with_protection(
            symbol, m1, h1,
            sl_pct=0.005, risk=0.01,
            rsi_low=15 if "boom" in symbol.lower() else 10,
            rsi_high=85 if "crash" in symbol.lower() else 90,
            max_hold=60, zone_mult=cfg["zone_mult"],
            use_tick_protection=False,
        )
        _print_result(r_base, "No protection")
        
        # ── With tick protection ──
        print(f"\n  [2] WITH TICK PROTECTION:")
        best = None
        best_score = -999
        for threshold in [10, 15, 20, 25]:
            r = run_smc_with_protection(
                symbol, m1, h1,
                sl_pct=0.005, risk=0.01,
                rsi_low=15 if "boom" in symbol.lower() else 10,
                rsi_high=85 if "crash" in symbol.lower() else 90,
                max_hold=60, zone_mult=cfg["zone_mult"],
                use_tick_protection=True, tick_threshold=threshold,
            )
            if r["trades"] >= 3:
                score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"])
                if score > best_score:
                    best_score = score
                    best = (threshold, r)
        
        if best:
            _print_result(best[1], f"Threshold={best[0]}")
        
        # ── Summary ──
        print(f"\n  {'='*50}")
        print(f"  SUMMARY: {symbol}")
        print(f"  {'='*50}")
        
        results = [("No protection", r_base)]
        if best:
            results.append((f"Tick prot={best[0]}", best[1]))
        
        for label, r in results:
            if r["trades"] > 0:
                prot = r.get("exit_reasons", {}).get("tick_protection", {}).get("pnl", 0)
                print(f"    {label:25s}  {r['trades']:3d}T  WR={r['win_rate']:.1f}%  "
                      f"PnL=${r['total_pnl']:+.2f}  DD={r['max_dd']:.1f}%"
                      f"{'  (saved $'+str(round(prot,2))+')' if prot > 0 else ''}")


def _print_result(r, label):
    """Print result."""
    if r["trades"] == 0:
        print(f"    {label}: No trades")
        return
    
    print(f"    {label}")
    print(f"      Trades:    {r['trades']}")
    print(f"      Win Rate:  {r['win_rate']:.1f}%")
    print(f"      PF:        {r['profit_factor']:.2f}")
    print(f"      PnL:       ${r['total_pnl']:+.2f}")
    print(f"      Max DD:    {r['max_dd']:.1f}%")
    
    if r["exit_reasons"]:
        print(f"      Exits:")
        for reason, stats in sorted(r["exit_reasons"].items()):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            marker = " [PROTECTION]" if reason == "tick_protection" else ""
            print(f"        {reason:15s}: {stats['count']:3d}T, PnL=${stats['pnl']:+.2f}, WR={wr:.0f}%{marker}")


if __name__ == "__main__":
    main()
