#!/usr/bin/env python3
"""
Risk & Probability Analysis

New Concepts:
1. Risk Metrics — Sharpe, Sortino, Calmar ratios
2. Drawdown Analysis — Max drawdown, recovery time
3. Risk of Ruin — Probability of losing everything
4. Monte Carlo Simulation — Simulate future scenarios
5. Bayesian Inference — Update beliefs with new data
6. Win Streak Analysis — Consecutive wins/losses
7. Optimal Position Sizing — Fixed vs fractional vs Kelly
"""

import pandas as pd
import numpy as np
import os
from collections import Counter

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time']) if 'time' in df.columns else df.index
    df['hour'] = pd.to_datetime(df['time']).dt.hour
    df['range'] = df['high'] - df['low']
    df['body'] = abs(df['close'] - df['open'])
    df['dir'] = np.sign(df['close'] - df['open'])
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1
    df['spike_size'] = df['range'] / med
    return df

def run_trades(df, cold_hours=None):
    """Run strategy and return list of trade PnLs."""
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    hours = df['hour'].values
    n = len(prices)
    trades = []
    cooldown_until = 0
    
    for i in range(n):
        if not is_spike[i] or i < cooldown_until:
            continue
        if cold_hours is not None and hours[i] not in cold_hours:
            continue
        
        entry_idx = i + 2
        if entry_idx >= n:
            continue
        
        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        exit_idx = None
        exit_price = None
        
        for j in range(entry_idx + 1, min(entry_idx + 7, n)):
            if (j - entry_idx) >= 6:
                exit_idx, exit_price = j, prices[j]
                break
            pnl_pct = ((entry_price - prices[j]) / entry_price) if direction == 1 else ((prices[j] - entry_price) / entry_price)
            if pnl_pct >= 0.004:
                exit_idx, exit_price = j, prices[j]
                break
            if is_spike[j] and spike_dir[j] == direction:
                exit_idx, exit_price = j, prices[j]
                break
        
        if exit_idx is None:
            exit_idx = min(entry_idx + 6, n - 1)
            exit_price = prices[exit_idx]
        
        pnl = ((entry_price - exit_price) / entry_price) if direction == 1 else ((exit_price - entry_price) / entry_price)
        trades.append(pnl)
        cooldown_until = entry_idx + 1
    
    return np.array(trades)

# =============================================================================
# CONCEPT 1: Risk Metrics
# =============================================================================
def calc_risk_metrics(trades, risk_free=0, periods_per_year=252*24*60):
    """Calculate Sharpe, Sortino, Calmar ratios."""
    if len(trades) < 10:
        return {}
    
    mean_ret = np.mean(trades)
    std_ret = np.std(trades)
    
    # Sharpe Ratio (annualized)
    sharpe = (mean_ret - risk_free) / std_ret * np.sqrt(periods_per_year) if std_ret > 0 else 0
    
    # Sortino Ratio (downside deviation only)
    downside = trades[trades < 0]
    downside_std = np.std(downside) if len(downside) > 0 else std_ret
    sortino = (mean_ret - risk_free) / downside_std * np.sqrt(periods_per_year) if downside_std > 0 else 0
    
    # Max Drawdown
    cumulative = np.cumsum(trades)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = running_max - cumulative
    max_dd = np.max(drawdown)
    
    # Calmar Ratio (annualized return / max drawdown)
    annual_return = mean_ret * periods_per_year
    calmar = annual_return / max_dd if max_dd > 0 else 0
    
    return {
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'max_dd': max_dd,
        'annual_return': annual_return,
        'mean': mean_ret,
        'std': std_ret
    }

# =============================================================================
# CONCEPT 2: Drawdown Analysis
# =============================================================================
def analyze_drawdowns(trades):
    """Analyze drawdown patterns."""
    if len(trades) < 10:
        return {}
    
    cumulative = np.cumsum(trades)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = running_max - cumulative
    
    # Find drawdown periods
    in_dd = False
    dd_start = 0
    dd_durations = []
    dd_depths = []
    
    for i in range(len(drawdown)):
        if drawdown[i] > 0 and not in_dd:
            in_dd = True
            dd_start = i
        elif drawdown[i] == 0 and in_dd:
            in_dd = False
            dd_durations.append(i - dd_start)
            dd_depths.append(np.max(drawdown[dd_start:i]))
    
    # If still in drawdown at end
    if in_dd:
        dd_durations.append(len(drawdown) - dd_start)
        dd_depths.append(np.max(drawdown[dd_start:]))
    
    print(f"\n  Drawdown Analysis:")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*25} {'-'*15}")
    print(f"  {'Max Drawdown':<25} {np.max(drawdown):>15.4f}%")
    print(f"  {'Avg Drawdown':<25} {np.mean(dd_depths) if dd_depths else 0:>15.4f}%")
    print(f"  {'Max DD Duration':<25} {max(dd_durations) if dd_durations else 0:>15} bars")
    print(f"  {'Avg DD Duration':<25} {np.mean(dd_durations) if dd_durations else 0:>15.1f} bars")
    print(f"  {'Num Drawdowns':<25} {len(dd_durations):>15}")
    
    return {'max_dd': np.max(drawdown), 'durations': dd_durations, 'depths': dd_depths}

# =============================================================================
# CONCEPT 3: Risk of Ruin
# =============================================================================
def calc_risk_of_ruin(trades, ruin_pct=0.5, simulations=10000):
    """Monte Carlo simulation for risk of ruin."""
    if len(trades) < 20:
        return {}
    
    mean_ret = np.mean(trades)
    std_ret = np.std(trades)
    n_trades = len(trades)
    
    # Run simulations
    ruined = 0
    max_drawdowns = []
    final_equities = []
    
    for _ in range(simulations):
        # Randomly sample trades with replacement
        sim_trades = np.random.choice(trades, size=n_trades, replace=True)
        equity = np.cumsum(sim_trades)
        
        # Check if ruined (dropped below ruin_pct of initial)
        if np.min(equity) < -ruin_pct:
            ruined += 1
        
        max_dd = np.max(np.maximum.accumulate(equity) - equity)
        max_drawdowns.append(max_dd)
        final_equities.append(equity[-1])
    
    risk_of_ruin = ruined / simulations * 100
    
    print(f"\n  Risk of Ruin Analysis ({simulations} simulations):")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*25} {'-'*15}")
    print(f"  {'Risk of Ruin (50% DD)':<25} {risk_of_ruin:>14.1f}%")
    print(f"  {'Avg Max Drawdown':<25} {np.mean(max_drawdowns):>15.4f}%")
    print(f"  {'95th %ile Max DD':<25} {np.percentile(max_drawdowns, 95):>15.4f}%")
    print(f"  {'Avg Final Equity':<25} {np.mean(final_equities):>15.4f}%")
    print(f"  {'5th %ile Final Equity':<25} {np.percentile(final_equities, 5):>15.4f}%")
    print(f"  {'95th %ile Final Equity':<25} {np.percentile(final_equities, 95):>15.4f}%")
    
    return {'risk_of_ruin': risk_of_ruin, 'max_drawdowns': max_drawdowns}

# =============================================================================
# CONCEPT 4: Monte Carlo Simulation
# =============================================================================
def monte_carlo_forward(trades, forward_trades=100, simulations=10000):
    """Simulate future performance."""
    if len(trades) < 20:
        return {}
    
    mean_ret = np.mean(trades)
    std_ret = np.std(trades)
    
    # Run simulations
    final_equities = []
    win_rates = []
    
    for _ in range(simulations):
        sim_trades = np.random.normal(mean_ret, std_ret, forward_trades)
        equity = np.cumsum(sim_trades)
        final_equities.append(equity[-1])
        win_rates.append((sim_trades > 0).sum() / forward_trades * 100)
    
    print(f"\n  Monte Carlo Forward Simulation ({forward_trades} trades, {simulations} sims):")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*25} {'-'*15}")
    print(f"  {'Median Final PnL':<25} {np.median(final_equities):>15.4f}%")
    print(f"  {'Mean Final PnL':<25} {np.mean(final_equities):>15.4f}%")
    print(f"  {'5th %ile (worst)':<25} {np.percentile(final_equities, 5):>15.4f}%")
    print(f"  {'95th %ile (best)':<25} {np.percentile(final_equities, 95):>15.4f}%")
    print(f"  {'Prob of Profit':<25} {sum(1 for e in final_equities if e > 0)/simulations*100:>14.1f}%")
    print(f"  {'Median Win Rate':<25} {np.median(win_rates):>14.1f}%")
    
    return {'median_pnl': np.median(final_equities), 'prob_profit': sum(1 for e in final_equities if e > 0)/simulations*100}

# =============================================================================
# CONCEPT 5: Bayesian Inference
# =============================================================================
def bayesian_update(prior_wr=0.5, prior_trades=10, observed_wr=None, observed_trades=None):
    """Update win rate belief using Bayesian inference."""
    if observed_wr is None or observed_trades is None:
        return {}
    
    # Beta distribution parameters
    # Prior: Beta(alpha, beta) where alpha = wins, beta = losses
    alpha_prior = prior_wr * prior_trades
    beta_prior = (1 - prior_wr) * prior_trades
    
    # Observed data
    observed_wins = observed_wr * observed_trades
    observed_losses = (1 - observed_wr) * observed_trades
    
    # Posterior: Beta(alpha + wins, beta + losses)
    alpha_post = alpha_prior + observed_wins
    beta_post = beta_prior + observed_losses
    
    # Posterior mean
    posterior_mean = alpha_post / (alpha_post + beta_post)
    
    # 95% credible interval
    from scipy.stats import beta
    ci_low = beta.ppf(0.025, alpha_post, beta_post)
    ci_high = beta.ppf(0.975, alpha_post, beta_post)
    
    print(f"\n  Bayesian Inference (Win Rate Update):")
    print(f"  {'Parameter':<25} {'Value':>15}")
    print(f"  {'-'*25} {'-'*15}")
    print(f"  {'Prior WR':<25} {prior_wr:>14.1%}")
    print(f"  {'Prior Trades':<25} {prior_trades:>15}")
    print(f"  {'Observed WR':<25} {observed_wr:>14.1%}")
    print(f"  {'Observed Trades':<25} {observed_trades:>15}")
    print(f"  {'Posterior Mean WR':<25} {posterior_mean:>14.1%}")
    print(f"  {'95% Credible Interval':<25} [{ci_low:.1%}, {ci_high:.1%}]")
    print(f"  {'Posterior Std':<25} {np.sqrt(alpha_post*beta_post/((alpha_post+beta_post)**2*(alpha_post+beta_post+1))):>15.4f}")
    
    return {'posterior_mean': posterior_mean, 'ci_low': ci_low, 'ci_high': ci_high}

# =============================================================================
# CONCEPT 6: Win Streak Analysis
# =============================================================================
def analyze_streaks(trades):
    """Analyze winning and losing streaks."""
    if len(trades) < 10:
        return {}
    
    results = trades > 0
    
    # Find streaks
    streaks = []
    current_streak = 1
    current_type = results[0]
    
    for i in range(1, len(results)):
        if results[i] == current_type:
            current_streak += 1
        else:
            streaks.append((current_type, current_streak))
            current_streak = 1
            current_type = results[i]
    streaks.append((current_type, current_streak))
    
    win_streaks = [s[1] for s in streaks if s[0]]
    loss_streaks = [s[1] for s in streaks if not s[0]]
    
    print(f"\n  Win/Loss Streak Analysis:")
    print(f"  {'Metric':<25} {'Value':>15}")
    print(f"  {'-'*25} {'-'*15}")
    print(f"  {'Max Win Streak':<25} {max(win_streaks) if win_streaks else 0:>15}")
    print(f"  {'Avg Win Streak':<25} {np.mean(win_streaks) if win_streaks else 0:>15.1f}")
    print(f"  {'Max Loss Streak':<25} {max(loss_streaks) if loss_streaks else 0:>15}")
    print(f"  {'Avg Loss Streak':<25} {np.mean(loss_streaks) if loss_streaks else 0:>15.1f}")
    print(f"  {'Total Streaks':<25} {len(streaks):>15}")
    
    return {'max_win': max(win_streaks) if win_streaks else 0, 'max_loss': max(loss_streaks) if loss_streaks else 0}

# =============================================================================
# CONCEPT 7: Optimal Position Sizing
# =============================================================================
def test_position_sizing(trades, initial_capital=100, risk_per_trade=0.02):
    """Compare fixed vs fractional vs Kelly sizing."""
    if len(trades) < 20:
        return {}
    
    print(f"\n  Position Sizing Comparison (Initial: ${initial_capital}):")
    
    # Fixed sizing (1 unit per trade)
    equity_fixed = initial_capital + np.cumsum(trades * initial_capital * 0.02)
    
    # Fractional sizing (2% risk)
    equity_fractional = [initial_capital]
    for pnl in trades:
        risk_amount = equity_fractional[-1] * risk_per_trade
        equity_fractional.append(equity_fractional[-1] + pnl * initial_capital * 0.02)
    equity_fractional = np.array(equity_fractional[1:])
    
    # Kelly sizing
    mean_ret = np.mean(trades)
    std_ret = np.std(trades)
    kelly = mean_ret / (std_ret ** 2) if std_ret > 0 else 0
    kelly = max(0, min(kelly, 0.5))  # Cap at 50%
    
    equity_kelly = [initial_capital]
    for pnl in trades:
        risk_amount = equity_kelly[-1] * kelly * 0.5  # Half-Kelly
        equity_kelly.append(equity_kelly[-1] + pnl * risk_amount)
    equity_kelly = np.array(equity_kelly[1:])
    
    print(f"  {'Method':<20} {'Final':>12} {'Max DD':>12} {'Sharpe':>12}")
    print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*12}")
    
    for name, eq in [('Fixed 2%', equity_fixed), ('Fractional 2%', equity_fractional), (f'Half-Kelly ({kelly*100:.1f}%)', equity_kelly)]:
        final = eq[-1]
        dd = np.max(np.maximum.accumulate(eq) - eq)
        rets = np.diff(eq) / eq[:-1]
        sharpe = np.mean(rets) / np.std(rets) * np.sqrt(252*24*60) if np.std(rets) > 0 else 0
        print(f"  {name:<20} ${final:>10.2f} {dd:>11.2f}% {sharpe:>12.2f}")
    
    return {'kelly': kelly}

# =============================================================================
# MAIN
# =============================================================================
def main():
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    cold_hour_map = {
        'boom500': [1, 5, 12, 14, 15, 19],
        'crash500': [0, 6, 7, 9, 22, 23],
        'boom1000': [6, 13, 18, 20, 21, 22],
        'crash1000': [3, 6, 7, 18, 22, 23],
    }
    
    print("=" * 100)
    print("  RISK & PROBABILITY ANALYSIS — BOOM/CRASH INDICES")
    print("=" * 100)
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        df = detect_spikes(df, mult=3.0)
        cold = cold_hour_map[sym]
        trades = run_trades(df, cold)
        
        if len(trades) < 20:
            continue
        
        print(f"\n{'='*100}")
        print(f"  {sym.upper()} ({len(trades)} trades)")
        print(f"{'='*100}")
        
        # Run all analyses
        metrics = calc_risk_metrics(trades)
        print(f"\n  Risk Metrics:")
        print(f"  {'Metric':<25} {'Value':>15}")
        print(f"  {'-'*25} {'-'*15}")
        print(f"  {'Sharpe Ratio':<25} {metrics.get('sharpe', 0):>15.2f}")
        print(f"  {'Sortino Ratio':<25} {metrics.get('sortino', 0):>15.2f}")
        print(f"  {'Calmar Ratio':<25} {metrics.get('calmar', 0):>15.2f}")
        print(f"  {'Annual Return':<25} {metrics.get('annual_return', 0):>14.2f}%")
        print(f"  {'Max Drawdown':<25} {metrics.get('max_dd', 0):>14.4f}%")
        
        analyze_drawdowns(trades)
        calc_risk_of_ruin(trades)
        monte_carlo_forward(trades)
        
        # Bayesian update with current data
        observed_wr = (trades > 0).mean()
        bayesian_update(prior_wr=0.5, prior_trades=10, observed_wr=observed_wr, observed_trades=len(trades))
        
        analyze_streaks(trades)
        test_position_sizing(trades)
    
    print(f"\n{'='*100}")
    print(f"  SUMMARY: KEY RISK INSIGHTS")
    print(f"{'='*100}")
    print(f"""
  1. SHARPE RATIO: Risk-adjusted returns
     - >1.0 = Good
     - >2.0 = Excellent
     - <0.5 = Poor
  
  2. SORTINO RATIO: Downside risk-adjusted
     - >1.5 = Good (focuses on losses)
  
  3. MAX DRAWDOWN: Worst peak-to-trough
     - <10% = Conservative
     - 10-20% = Moderate
     - >20% = Aggressive
  
  4. RISK OF RUIN: Probability of 50% loss
     - <5% = Safe
     - 5-10% = Moderate risk
     - >10% = High risk
  
  5. MONTE CARLO: Forward simulation
     - Median PnL = expected outcome
     - 5th %ile = worst case
  
  6. BAYESIAN: Update beliefs
     - Prior + Data = Posterior
     - Credible interval = uncertainty
  
  7. WIN STREAKS: Consecutive patterns
     - Max win/loss streak
     - Helps with psychology
  
  8. POSITION SIZING: Optimal bet size
     - Fixed: Simple but suboptimal
     - Fractional: Better risk management
     - Kelly: Optimal but aggressive
    """)
    
    print(f"  Analysis complete.")
    print(f"{'='*100}")

if __name__ == '__main__':
    main()
