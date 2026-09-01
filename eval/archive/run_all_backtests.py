"""
eval/run_all_backtests.py — Run all major strategy backtests and compare.
"""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def run_strategy(name, func, *args, **kwargs):
    """Run a strategy backtest and return results."""
    try:
        result = func(*args, **kwargs)
        return {"name": name, "status": "ok", "result": result}
    except Exception as e:
        return {"name": name, "status": "error", "error": str(e)}


def main():
    results = []
    
    # ═══════════════════════════════════════════════════════════════
    # 1. SpikeCatcher v2 (already ran, include for comparison)
    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 72)
    print("  RUNNING ALL STRATEGY BACKTESTS")
    print("=" * 72)
    
    # ═══════════════════════════════════════════════════════════════
    # 2. SMC Spike Hunter
    # ═══════════════════════════════════════════════════════════════
    print("\n--- SMC Spike Hunter ---")
    try:
        from eval.smc_spike_hunter import run_smc_backtest
        for sym in ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]:
            r = run_smc_backtest(sym)
            results.append({"strategy": "SMC Spike Hunter", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', 0) * 100
            ret = r.get('total_return_pct', 0)
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 3. Backtest Boom Crash (main strategy)
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Backtest Boom Crash ---")
    try:
        from eval.backtest_boom_crash import run_backtest
        for sym in ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]:
            r = run_backtest(sym)
            results.append({"strategy": "Backtest Boom Crash", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 4. Drift Fade Strategy
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Drift Fade Strategy ---")
    try:
        from eval.drift_fade_strategy import run_backtest as drift_run
        import pandas as pd
        for sym in ["BOOM500", "CRASH500"]:
            f = Path(__file__).parent.parent / "data" / f"candles_{sym.lower()}_1min.parquet"
            if f.exists():
                df = pd.read_parquet(f)
                r = drift_run(sym, df)
                results.append({"strategy": "Drift Fade", "symbol": sym, **r})
                n = r.get('trades', 0)
                wr = r.get('win_rate', r.get('wr', 0))
                if isinstance(wr, float) and wr <= 1:
                    wr *= 100
                ret = r.get('total_return_pct', r.get('return_pct', 0))
                print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 5. Dual Direction
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Dual Direction ---")
    try:
        from eval.dual_direction import run_backtest as dual_run
        for sym in ["CRASH500", "BOOM500"]:
            r = dual_run(symbol=sym, max_bars=20000)
            results.append({"strategy": "Dual Direction", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 6. Momentum Strategy
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Momentum Strategy ---")
    try:
        from eval.momentum_strategy import run_momentum_backtest
        for sym in ["BOOM500", "CRASH500"]:
            r = run_momentum_backtest(sym)
            results.append({"strategy": "Momentum", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 7. Multi-Timeframe
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Multi-Timeframe ---")
    try:
        from eval.multitimeframe import run_mtf_backtest
        for sym in ["BOOM500", "CRASH500"]:
            r = run_mtf_backtest(sym)
            results.append({"strategy": "Multi-Timeframe", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 8. Velocity Exhaustion
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Velocity Exhaustion ---")
    try:
        from eval.velocity_exhaustion import run_backtest as vel_run
        for sym in ["CRASH500", "BOOM500"]:
            r = vel_run(symbol=sym, max_bars=20000)
            results.append({"strategy": "Velocity Exhaustion", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 9. Sweet Spot Strategy
    # ═══════════════════════════════════════════════════════════════
    print("\n--- Sweet Spot Strategy ---")
    try:
        from eval.sweet_spot_strategy import run_full_backtest
        import pandas as pd
        for sym in ["BOOM500", "CRASH500"]:
            f = Path(__file__).parent.parent / "data" / f"candles_{sym.lower()}_1min.parquet"
            if f.exists():
                df = pd.read_parquet(f)
                prices = df['close'].values
                r = run_full_backtest(sym, prices)
                results.append({"strategy": "Sweet Spot", "symbol": sym, **r})
                n = r.get('trades', 0)
                wr = r.get('win_rate', r.get('wr', 0))
                if isinstance(wr, float) and wr <= 1:
                    wr *= 100
                ret = r.get('total_return_pct', r.get('return_pct', 0))
                print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # 10. SMC Barcount v3
    # ═══════════════════════════════════════════════════════════════
    print("\n--- SMC Barcount v3 ---")
    try:
        from eval.smc_barcount_v3 import run_backtest as smc3_run
        for sym in ["BOOM500", "CRASH500"]:
            r = smc3_run(sym, sym)
            results.append({"strategy": "SMC Barcount v3", "symbol": sym, **r})
            n = r.get('trades', 0)
            wr = r.get('win_rate', r.get('wr', 0))
            if isinstance(wr, float) and wr <= 1:
                wr *= 100
            ret = r.get('total_return_pct', r.get('return_pct', 0))
            print(f"  {sym}: {n}T WR={wr:.1f}% Ret={ret:+.2f}%")
    except Exception as e:
        print(f"  ERROR: {e}")
        traceback.print_exc()
    
    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print("\n\n" + "=" * 90)
    print("  COMPREHENSIVE BACKTEST COMPARISON")
    print("=" * 90)
    print(f"{'Strategy':<25} {'Symbol':<12} {'Trades':>7} {'WR%':>7} {'Return':>10} {'PF':>7} {'MaxDD':>8}")
    print("-" * 90)
    
    # Sort by return
    valid = [r for r in results if r.get('trades', 0) > 0]
    valid.sort(key=lambda x: x.get('total_return_pct', x.get('return_pct', 0)), reverse=True)
    
    for r in valid:
        strat = r.get('strategy', '?')
        sym = r.get('symbol', '?')
        n = r.get('trades', 0)
        wr = r.get('win_rate', r.get('wr', 0))
        if isinstance(wr, float) and wr <= 1:
            wr *= 100
        ret = r.get('total_return_pct', r.get('return_pct', 0))
        pf = r.get('profit_factor', r.get('pf', 0))
        dd = r.get('max_dd_pct', r.get('max_dd', 0))
        print(f"{strat:<25} {sym:<12} {n:>7} {wr:>6.1f}% {ret:>+9.2f}% {pf:>7.2f} {dd:>7.2f}%")
    
    # Also show no-trade strategies
    no_trade = [r for r in results if r.get('trades', 0) == 0]
    if no_trade:
        print(f"\n  No trades: {', '.join(r.get('strategy', '?') + ' (' + r.get('symbol', '?') + ')' for r in no_trade)}")
    
    # Errors
    errors = [r for r in results if r.get('status') == 'error']
    if errors:
        print(f"\n  Errors: {', '.join(r.get('strategy', '?') + ': ' + r.get('error', '?')[:50] for r in errors)}")


if __name__ == "__main__":
    main()
