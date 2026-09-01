"""Analyze SL slippage impact on BOOM500."""
import sys, numpy as np
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles

m1 = load_candles('BOOM500', '1min')
closes = np.array([b['close'] for b in m1], dtype=float)
price = closes[-1]

# Median volatility
pct_changes = np.abs(np.diff(closes) / closes[:-1])
median_vol = np.median(pct_changes)

# Find spikes
spike_sizes = []
spike_actual_pcts = []
for i in range(100, len(pct_changes)):
    window = pct_changes[i-100:i]
    med = max(np.median(window), 0.0001)
    if pct_changes[i] / med >= 3.0:
        spike_sizes.append(pct_changes[i] / med)
        spike_actual_pcts.append(pct_changes[i])

spike_sizes = np.array(spike_sizes)
spike_actual_pcts = np.array(spike_actual_pcts)

print("=" * 70)
print("  SLIPPAGE IMPACT ANALYSIS — BOOM500")
print("=" * 70)
print(f"  Price: {price:.2f}")
print(f"  Total spikes (3x+): {len(spike_sizes)}")
print(f"  Median spike size: {np.median(spike_sizes):.1f}x median vol")
print(f"  Mean spike size:   {np.mean(spike_sizes):.1f}x median vol")
print(f"  P90 spike:         {np.percentile(spike_sizes, 90):.1f}x")
print(f"  P95 spike:         {np.percentile(spike_sizes, 95):.1f}x")
print(f"  Max spike:         {np.max(spike_sizes):.1f}x")

# Actual price movement
print(f"\n  ACTUAL SPIKE MOVEMENTS:")
print(f"    Median: {np.median(spike_actual_pcts)*100:.3f}% = {price*np.median(spike_actual_pcts):.1f} points")
print(f"    Mean:   {np.mean(spike_actual_pcts)*100:.3f}% = {price*np.mean(spike_actual_pcts):.1f} points")
print(f"    P90:    {np.percentile(spike_actual_pcts, 90)*100:.3f}% = {price*np.percentile(spike_actual_pcts, 90):.1f} points")
print(f"    P95:    {np.percentile(spike_actual_pcts, 95)*100:.3f}% = {price*np.percentile(spike_actual_pcts, 95):.1f} points")

# Our SL
sl_pct = 0.005
sl_points = price * sl_pct
print(f"\n  OUR STOP LOSS: {sl_pct*100:.1f}% = {sl_points:.1f} points")

# How often does spike blow through SL?
blow_through = np.sum(spike_actual_pcts > sl_pct) / len(spike_actual_pcts) * 100
print(f"  Spikes bigger than our SL: {blow_through:.1f}%")

# Slippage per SL hit
print(f"\n  SLIPPAGE WHEN SPIKE HITS SL (at 0.01 lot):")
for label, pct in [("Average spike", np.mean(spike_actual_pcts)),
                    ("P90 spike", np.percentile(spike_actual_pcts, 90)),
                    ("P95 spike", np.percentile(spike_actual_pcts, 95))]:
    spike_pts = price * pct
    slippage_pts = max(0, spike_pts - sl_points)
    expected_loss = sl_points * 0.01
    actual_loss = spike_pts * 0.01
    print(f"    {label:15s}: fill at {spike_pts:.1f} pts vs SL {sl_points:.1f} pts")
    print(f"                      slippage: +{slippage_pts:.1f} pts = ${slippage_pts*0.01:.4f}")
    print(f"                      expected: ${expected_loss:.4f}  actual: ${actual_loss:.4f}  ({actual_loss/expected_loss*100:.0f}%)")

# Monthly impact
print(f"\n  MONTHLY IMPACT ON $10 ACCOUNT:")
trades_per_month = 105
wr = 0.24
avg_win_pct = 0.0048
sl_hits_pct = 0.70  # 70% of losses are SL hits

# Without slippage
wins = trades_per_month * wr
losses = trades_per_month * (1 - wr)
pnl_wins = wins * avg_win_pct * price * 0.01
pnl_losses = losses * (-0.0009 * price * 0.01)
pnl_no_slip = pnl_wins + pnl_losses

# With slippage (SL fills at spike level)
n_sl = losses * sl_hits_pct
avg_slippage = np.mean(spike_actual_pcts) * price * 0.01
extra_slippage = n_sl * max(0, avg_slippage - sl_points * 0.01)
pnl_with_slip = pnl_no_slip - extra_slippage

print(f"    Without slippage: ${pnl_no_slip:+.4f}/month")
print(f"    Slippage cost:    ${extra_slippage:.4f}/month ({n_sl:.0f} SL trades)")
print(f"    With slippage:    ${pnl_with_slip:+.4f}/month")
print(f"    Monthly return on $10: {pnl_with_slip/10*100:+.2f}%")

# Is strategy still profitable?
if pnl_with_slip > 0:
    print(f"\n  VERDICT: STILL PROFITABLE after slippage")
else:
    print(f"\n  VERDICT: SLIPPAGE MAKES STRATEGY UNPROFITABLE")
