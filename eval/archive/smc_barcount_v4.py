"""SMC + Bar Count combined strategy."""
import sys
import numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.smc_spike_hunter import SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks

print("=" * 70)
print("  SMC + BAR COUNT — LOWER THRESHOLDS")
print("=" * 70)

for symbol, candle_sym in [("Boom 500 Index", "boom500"), ("Crash 500 Index", "crash500")]:
    is_boom = "boom" in symbol.lower()
    is_crash = "crash" in symbol.lower()

    m1 = load_candles(candle_sym, "1min")
    h1 = load_candles(candle_sym, "60min")
    zones = detect_order_blocks(h1, threshold_mult=2.0 if is_crash else 2.5)

    closes = np.array([b["close"] for b in m1], dtype=float)

    # Detect spikes
    changes = np.abs(np.diff(closes))
    positive = changes[changes > 0]
    median = np.median(positive)
    spike_indices = set(np.where(changes > median * 3.0)[0].tolist())

    # Bars since spike
    bars_since = np.zeros(len(m1), dtype=int)
    count = 0
    for i in range(len(m1)):
        if (i - 1) in spike_indices:
            count = 0
        else:
            count += 1
        bars_since[i] = count

    print(f"\n  {symbol}: max bars since spike = {bars_since.max()}")

    # SMC entries with bar count
    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=0.01,
        rsi_extreme_low=15 if is_boom else 10,
        rsi_extreme_high=85 if is_crash else 90,
        entry_slippage_pct=0.00011, use_scaling=False,
    )
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    entries = []
    for i, bar in enumerate(m1):
        result = smc.on_bar(bar, i)
        if result and result.get("action") == "open":
            entries.append({"idx": i, "price": result["entry_price"],
                          "side": result["side"], "bars": int(bars_since[i])})

    # Full SMC for trades
    smc2 = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc2.set_h1_zones(zones)
    smc2.precompute(m1)
    for i, bar in enumerate(m1):
        smc2.on_bar(bar, i)
    trades = smc2.trade_log

    # Show entry bar counts
    bar_counts = [e["bars"] for e in entries]
    print(f"  SMC entries: {len(entries)}")
    print(f"  Bar count distribution at entry:")
    for lo, hi in [(0,5), (5,10), (10,15), (15,20), (20,30), (30,50), (50,100)]:
        c = sum(1 for b in bar_counts if lo <= b < hi)
        if c > 0:
            print(f"    {lo:3d}-{hi:3d} bars: {c} entries")

    # Test skip filters
    print(f"\n  Filter: skip if bars_since_spike < N")
    print(f"  {'Skip':>6s}  {'Trades':>7s}  {'WR':>6s}  {'PF':>6s}  {'PnL':>9s}  {'Ret':>8s}")
    print(f"  {'-'*6}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*9}  {'-'*8}")

    best_pnl = -999
    best_skip = 0

    for skip in [0, 1, 2, 3, 5, 8, 10, 15, 20]:
        filtered = []
        for trade in trades:
            ep = trade["entry_price"]
            for entry in entries:
                if abs(entry["price"] - ep) < 0.01 and entry["bars"] >= skip:
                    filtered.append(trade)
                    break

        if not filtered:
            print(f"  {skip:>6d}  {'0':>7s}")
            continue

        pnls = [t["pnl"] for t in filtered]
        n = len(filtered)
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100
        tw = sum(p for p in pnls if p > 0)
        tl = abs(sum(p for p in pnls if p < 0))
        pf = tw / tl if tl > 0 else 0
        pnl = sum(pnls)
        ret = pnl / 10000 * 100

        marker = ""
        if pnl > best_pnl:
            best_pnl = pnl
            best_skip = skip
            marker = " *"

        print(f"  {skip:>6d}  {n:>7d}  {wr:>5.1f}%  {pf:>5.2f}  ${pnl:>+8.2f}  {ret:>+7.2f}%{marker}")

    if best_pnl > -999:
        filtered = []
        for trade in trades:
            ep = trade["entry_price"]
            for entry in entries:
                if abs(entry["price"] - ep) < 0.01 and entry["bars"] >= best_skip:
                    filtered.append(trade)
                    break
        if filtered:
            pnls = [t["pnl"] for t in filtered]
            n = len(filtered)
            wins = sum(1 for p in pnls if p > 0)
            lot = 0.20
            risk = lot * 5
            win_amt = lot * 15
            monthly = wins * win_amt - (n - wins) * risk
            print(f"\n  BEST: skip <{best_skip} bars -> {n} trades, WR={wins/n*100:.1f}%, PnL=${best_pnl:+.2f}")
            print(f"    Monthly: ${monthly:.2f}  Annual: ${monthly*12:.2f}")
