#!/usr/bin/env python3
"""
Clean up redundant eval scripts.
Keeps only verified/essential scripts, archives the rest.
"""

import os
import shutil
from pathlib import Path

# Scripts to KEEP (verified or essential)
KEEP_SCRIPTS = {
    # Core verification
    "final_backtest.py",           # Verified comprehensive backtest
    "backtest_cold_hours.py",      # Verified COLD hours
    "backtest_cold_tickcombo.py",  # Verified COLD + tick exit
    "check_decay.py",              # Pattern decay checking
    "cold_hours_stability.py",     # COLD hours stability
    
    # Spike analysis
    "find_spike_predictors.py",    # Spike predictor analysis
    "prespike_features.py",        # Pre-spike feature analysis
    "analyze_spike_speed.py",      # Spike speed analysis
    "test_velocity_12.py",         # Velocity pattern test
    "backtest_velocity_1000.py",   # BOOM/CRASH 1000 velocity
    
    # Verification
    "verify_crash300.py",          # Crash 300 verification
    "backtest_spikecatcher_v2.py", # SpikeCatcher v2 backtest
    
    # Paper trading
    "paper_trading_tracker.py",    # Paper trading tracking
    
    # Utilities
    "__init__.py",                 # Package init
}

# Archive directory
ARCHIVE_DIR = Path("eval/archive")

def cleanup():
    """Move redundant scripts to archive."""
    eval_dir = Path("eval")
    
    # Create archive directory
    ARCHIVE_DIR.mkdir(exist_ok=True)
    
    moved = 0
    kept = 0
    
    for f in eval_dir.glob("*.py"):
        if f.name in KEEP_SCRIPTS:
            kept += 1
            print(f"  [KEEP] {f.name}")
        else:
            # Move to archive
            dest = ARCHIVE_DIR / f.name
            if dest.exists():
                dest.unlink()
            shutil.move(str(f), str(dest))
            moved += 1
            print(f"  [ARCHIVE] {f.name}")
    
    # Also move __pycache__
    pycache = eval_dir / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
        print(f"  [REMOVED] __pycache__/")
    
    # Move txt files to archive
    for f in eval_dir.glob("*.txt"):
        dest = ARCHIVE_DIR / f.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(f), str(dest))
        moved += 1
        print(f"  [ARCHIVE] {f.name}")
    
    print(f"\n{'='*50}")
    print(f"  CLEANUP COMPLETE")
    print(f"{'='*50}")
    print(f"  Kept: {kept} scripts")
    print(f"  Archived: {moved} scripts")
    print(f"  Archive location: {ARCHIVE_DIR}")

if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  EVAL FOLDER CLEANUP")
    print(f"{'='*50}\n")
    cleanup()
