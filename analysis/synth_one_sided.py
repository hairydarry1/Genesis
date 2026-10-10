#!/usr/bin/env python3
"""One-off: Trek buy&hold ownability + one-sided follower splits + RB100 asymmetry."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CFG = {
    "Trek_Up_Index":       (0.01, 1.04),
    "Trek_Down_Index":     (0.01, 0.72),
    "Range_Break_100_Index": (0.01, 2.6),
    "Vol_over_Crash_400":  (0.01, 1.11),
}

def follower(ret, big_set, dpu, spread, side):
    pos = cash = 0.0
    ld = con = 0
    for i in range(len(ret)):
        cash += pos * ret[i] * dpu
        if i in big_set:
            d = int(np.sign(ret[i]))
            ld, con = (d, con + 1) if d == ld else (d, 1)
            want = side * d if con >= 2 else 0
            if want != pos:
                cash -= spread * dpu
                pos = want
    if pos != 0:
        cash -= spread * dpu
    return cash

def main():
    for name, (dpu, spread) in CFG.items():
        p = f"data/synth_scan/bars30_{name}.npy"
        if not os.path.exists(p):
            print(name, "no cache"); continue
        b = np.load(p)
        o, c = b[:, 1], b[:, 4]
        bh = (c[-1] - o[0]) * dpu
        ret = np.diff(c)
        med = np.median(np.abs(ret[ret != 0]))
        big = np.where(np.abs(ret) > 3 * med)[0]
        big_set = set(big.tolist())
        d = np.sign(ret[big])
        lo = follower(ret, big_set, dpu, spread, 1)
        sh = follower(ret, big_set, dpu, spread, -1)
        print(f"{name:22s} buy&hold ${bh:>8.2f}/30d  long-follower ${lo:>8.2f}  "
              f"short-follower ${sh:>8.2f}  up-moves={int((d>0).sum())} dn-moves={int((d<0).sum())}")

if __name__ == "__main__":
    main()
