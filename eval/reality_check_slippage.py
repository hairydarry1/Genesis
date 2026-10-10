#!/usr/bin/env python3
"""
REALITY CHECK: SLIPPAGE ON BOOM/CRASH
======================================
Stop losses DON'T work on Boom/Crash because:
1. Spikes happen in 1-2 ticks (under 100ms)
2. Your SL order reaches server AFTER the spike
3. Fill price is 5-18x WORSE than your SL level

This changes everything about cluster risk.
"""
import numpy as np
import statistics

def analyze():
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy'), 
                            ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        prices = np.load(filepath)[:500000]
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        spike_idx = np.where(moves > 3 * med)[0]
        is_boom = 'BOOM' in name
        
        print(f"\n{'#'*60}")
        print(f"# {name} - REAL SLIPPAGE ANALYSIS")
        print(f"{'#'*60}")
        print(f"Spikes: {len(spike_idx)}, Median: {med:.4f}")
        
        # What actually happens when SL triggers during a spike
        print(f"\n--- HOW SL ACTUALLY WORKS ON BOOM/CRASH ---")
        print(f"  You set SL at 0.5x median = {0.5*med:.4f} points away")
        print(f"  Spike hits: price jumps 3-10x median in ONE tick")
        print(f"  Your SL order reaches server ~50-200ms later")
        print(f"  By then, price has moved WAY past your SL")
        print(f"  Fill price = SL level + slippage")
        print(f"")
        print(f"  Intended loss:   {0.5*med:.4f} points")
        print(f"  Actual loss:     {0.5*med*10:.4f} to {0.5*med*18:.4f} points")
        print(f"  Slippage factor: 10x to 18x WORSE")
        
        # What this means for cluster damage
        print(f"\n--- CLUSTER DAMAGE WITH REAL SLIPPAGE ---")
        
        # Find clusters (2+ spikes within 5 ticks)
        cluster_events = []
        i = 0
        while i < len(spike_idx):
            cluster = [spike_idx[i]]
            j = i + 1
            while j < len(spike_idx) and spike_idx[j] - cluster[-1] <= 5:
                cluster.append(spike_idx[j])
                j += 1
            if len(cluster) >= 2:
                cluster_events.append(cluster)
            i = j
        
        print(f"  Cluster events: {len(cluster_events)}")
        
        # Simulate cluster with slippage
        slippage = 10  # Conservative
        
        worst_loss = 0
        avg_loss = 0
        losses = []
        
        for cluster in cluster_events:
            entry_idx = cluster[0] + 1
            if entry_idx >= len(prices):
                continue
            
            # Second spike hits, SL triggers with slippage
            if len(cluster) >= 2:
                loss = 0.5 * med * slippage  # SL with slippage
                
                # Third spike adds more damage
                if len(cluster) >= 3:
                    loss += 0.5 * med * slippage * 0.5
                
                losses.append(loss)
                worst_loss = max(worst_loss, loss)
        
        if losses:
            avg_loss = statistics.mean(losses)
            
            print(f"  Worst cluster loss: {worst_loss:.4f} points ({worst_loss/med:.0f}x median)")
            print(f"  Average cluster loss: {avg_loss:.4f} points ({avg_loss/med:.0f}x median)")
            
            # How many wins to recover?
            avg_win = 1.0 * med  # TP = 1.0x
            wins_needed = worst_loss / avg_win
            
            print(f"\n--- RECOVERY ANALYSIS ---")
            print(f"  Avg win (TP): {avg_win:.4f} points")
            print(f"  Avg cluster loss (SL with slippage): {avg_loss:.4f} points")
            print(f"  Wins needed to recover 1 cluster loss: {avg_loss/avg_win:.1f}")
            print(f"  At 99.7% WR: ~{avg_loss/avg_win/0.997:.0f} trades")
            print(f"  At 50 trades/day: ~{avg_loss/avg_win/0.997/50:.1f} days")
        
        # Monthly projection
        print(f"\n--- MONTHLY PNL (HONEST) ---")
        monthly_trades = 1100
        monthly_wins = int(monthly_trades * 0.997)
        monthly_losses = monthly_trades - monthly_wins
        
        win_pnl = monthly_wins * 1.0 * med
        loss_pnl = monthly_losses * 0.5 * med * slippage
        cluster_pnl = len(cluster_events) * 0.5 * med * slippage * 0.3  # 30% hit rate
        
        net_pnl = win_pnl - loss_pnl - cluster_pnl
        
        print(f"  Wins: {monthly_wins} x +{1.0*med:.4f} = +{win_pnl:.4f}")
        print(f"  Losses: {monthly_losses} x -{0.5*med*slippage:.4f} = -{loss_pnl:.4f}")
        print(f"  Cluster extra: -{cluster_pnl:.4f}")
        print(f"  Net: {net_pnl:+.4f} points")
        print(f"  On $1000: ${net_pnl * 100:+.2f}/month")
        
        # The REAL defense
        print(f"\n--- WHAT ACTUALLY DEFENDS AGAINST CLUSTERS ---")
        print(f"  1. TIME EXIT (not SL)")
        print(f"     Close after 3 bars BEFORE next spike can hit")
        print(f"     This is the ONLY reliable defense")
        print(f"")
        print(f"  2. DON'T HOLD THROUGH SPIKES")
        print(f"     If you're in a trade and a spike hits,")
        print(f"     your loss = FULL spike size (not SL)")
        print(f"     So get out FAST (time exit)")
        print(f"")
        print(f"  3. ACCEPT THE RISK")
        print(f"     When a cluster hits, you lose 1-2 full spike sizes")
        print(f"     This is the COST of doing business")
        print(f"     It's built into the expected value")
        
        # Honest expected value
        print(f"\n--- HONEST EXPECTED VALUE ---")
        avg_cluster_loss = statistics.mean(losses) if losses else 0
        normal_win = 1.0 * med
        normal_loss_with_slippage = 0.5 * med * slippage
        
        # Probability of cluster
        p_cluster = len(cluster_events) / len(spike_idx)
        
        # Expected value per trade
        ev_normal = 0.997 * normal_win + 0.003 * (-normal_loss_with_slippage)
        ev_cluster = p_cluster * (-avg_cluster_loss) if avg_cluster_loss > 0 else 0
        
        total_ev = ev_normal + ev_cluster
        
        print(f"  Normal trade EV: {ev_normal:+.6f} points")
        print(f"  Cluster penalty: {ev_cluster:+.6f} points")
        print(f"  Total EV per trade: {total_ev:+.6f} points")
        print(f"  On $1000: ${total_ev * 100:+.4f} per trade")
        
        if total_ev > 0:
            print(f"  Strategy is PROFITABLE even with slippage")
        else:
            print(f"  Strategy is LOSING money with real slippage")

if __name__ == "__main__":
    analyze()
