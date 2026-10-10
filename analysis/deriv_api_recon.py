#!/usr/bin/env python3
"""Read-only Deriv WebSocket API recon for the Crash Boom Flip indices.

Questions (docs/FLIP_INDEX_MECHANICS.md, API-venue appendix):
  1. Are the Flip indices exposed via active_symbols (and under what codes)?
  2. Does contracts_for offer MULTUP/MULTDOWN on them? Extract: min/max stake,
     multiplier range, commission, stop-out level, deal-cancellation durations.
  3. Does ticks_history serve the same tick process as MT5? Compare median
     |delta| and spike rate (5x median) against the local MT5 archive.
  4. Is a live subscription (ticks_stream) available?

STRICTLY READ-ONLY in scope: only ping/active_symbols/contracts_for/ticks_history
are sent; a token (read scope) is only used for `authorize`, which cannot trade.

Auth reality (measured 2026-09-12):
  - ticks_history + tick streams are PUBLIC (BOOM1000/CRASH500/R_100 serve ticks
    unauthenticated).
  - active_symbols / asset_index return EMPTY LISTS unauthenticated (not an
    error) -> the symbol catalog needs a Deriv API token (read scope), which is
    a Deriv-account token, NOT the MT5 login.

Usage:
  python analysis/deriv_api_recon.py [TOKEN]
    TOKEN optional (or env DERIV_API_TOKEN). Without it: public-mode probe,
    catalog section will be empty by design.
"""
import json
import os
import sys
import time
from collections import Counter

import numpy as np
from websocket import create_connection

WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
OUT = "data/deriv_api_recon.json"
REQ_ID = [0]
TOKEN = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DERIV_API_TOKEN", "")


def call(ws, req, timeout=25):
    REQ_ID[0] += 1
    req["req_id"] = REQ_ID[0]
    ws.send(json.dumps(req))
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = json.loads(ws.recv())
        if msg.get("req_id") == req["req_id"]:
            if "error" in msg:
                return {"_error": msg["error"]}
            return msg
        # ignore stream/keepalive messages not ours
    return {"_error": {"code": "Timeout", "message": f"no reply in {timeout}s"}}


def main():
    results = {}

    ws = create_connection(WS_URL, timeout=25)
    try:
        # 0. ping
        results["ping"] = call(ws, {"ping": 1})

        # 0.5 authorize (optional, read-only use)
        if TOKEN:
            auth = call(ws, {"authorize": TOKEN})
            results["authorize_ok"] = "_error" not in auth
            if "_error" in auth:
                results["authorize_error"] = auth["_error"]
            else:
                a = auth.get("authorize", {})
                results["authorize_info"] = {
                    "loginid": a.get("loginid"),
                    "is_virtual": a.get("is_virtual"),
                    "currency": a.get("currency"),
                    "landing_company_fullname": a.get("landing_company_fullname"),
                }
        else:
            results["authorize_ok"] = False
            results["authorize_note"] = ("no token: catalog endpoints return "
                                         "empty by design; market data still works")

        # 1. active_symbols
        act = call(ws, {"active_symbols": "brief", "product_type": "basic"})
        results["active_symbols_ok"] = "_error" not in act
        syms = act.get("active_symbols", [])
        results["n_symbols_total"] = len(syms)

        def name(s):
            return f"{s.get('display_name','')} {s.get('market_display_name','')} {s.get('submarket_display_name','')}"

        flips = [s for s in syms if "flip" in name(s).lower()]
        booms = [s for s in syms if "boom" in name(s).lower()]
        crash = [s for s in syms if "crash" in name(s).lower()]
        results["flip_symbols"] = [
            {k: s.get(k) for k in ("symbol", "display_name", "market", "submarket",
                                   "exchange_is_open", "is_trading_suspended")}
            for s in flips
        ]
        results["boom_crash_count"] = len(booms) + len(crash)
        results["boom_symbols"] = [s["symbol"] for s in booms]
        results["crash_symbols"] = [s["symbol"] for s in crash]

        # 2. contracts_for on every flip symbol
        results["contracts"] = {}
        for s in flips:
            sym = s["symbol"]
            cf = call(ws, {"contracts_for": sym, "currency": "USD",
                           "product_type": "basic"})
            if "_error" in cf:
                results["contracts"][sym] = {"error": cf["_error"]}
                continue
            out = []
            for c in cf.get("contracts_for", {}).get("available", []):
                out.append({
                    "contract_type": c.get("contract_type"),
                    "contract_category": c.get("contract_category"),
                    "contract_category_display": c.get("contract_category_display"),
                    "min_stake": c.get("min_stake"),
                    "max_stake": c.get("max_stake"),
                    "multiplier_range": c.get("multiplier_range"),
                    "commission": c.get("commission"),
                    "stop_out_level": c.get("stop_out_level"),
                    "cancellation_range": c.get("cancellation_range"),
                    "limit_order": sorted((c.get("limit_order") or {}).keys()),
                    "net_profit_above": c.get("net_profit_above"),
                })
            results["contracts"][sym] = {
                "has_multup_multdown": any(
                    (c or {}).get("contract_type") in ("MULTUP", "MULTDOWN")
                    for c in out),
                "n_contracts": len(out),
                "contracts": out,
            }

        # 3. ticks_history on the first flip symbol -> feed parity vs MT5 archive
        results["feed"] = {}
        if flips:
            sym = flips[0]["symbol"]
            th = call(ws, {"ticks_history": sym, "adjust_start_time": 1,
                           "count": 1000, "end": "latest", "style": "ticks"})
            if "_error" in th:
                results["feed"][sym] = {"error": th["_error"]}
            else:
                prices = th.get("history", {}).get("prices", [])
                times = th.get("history", {}).get("times", [])
                px = np.asarray(prices, dtype=float)
                if len(px) >= 10:
                    d = np.diff(px)
                    med = float(np.median(np.abs(d)))
                    spikes = int(np.sum(np.abs(d) > 5.0 * med)) if med > 0 else 0
                    results["feed"][sym] = {
                        "n_ticks": len(px),
                        "t0_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(times[0])),
                        "tN_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(times[-1])),
                        "last_price": float(px[-1]),
                        "median_abs_delta": med,
                        "spikes_5x": spikes,
                        "tick_rate_per_min": round(len(px) / max(1e-9, (times[-1] - times[0]) / 60.0), 1),
                    }
                    # MT5 archive comparison (latest local file)
                    try:
                        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data"))
                        import glob as _g
                        files = sorted(_g.glob(os.path.join(
                            os.path.dirname(__file__), "..", "data", "flip_ticks",
                            "flip1000_*.npy")))
                        if files:
                            arr = np.load(files[-1])
                            d2 = np.diff(arr[:, 1])
                            med2 = float(np.median(np.abs(d2)))
                            results["feed"][sym]["mt5_archive"] = {
                                "file": os.path.basename(files[-1]),
                                "n_ticks": int(len(arr)),
                                "median_abs_delta": med2,
                                "spike_rate_pct": round(100 * float(np.mean(np.abs(d2) > 5.0 * med2)), 2),
                                "api_spike_rate_pct": round(100 * spikes / len(d), 2) if len(d) else None,
                            }
                    except Exception as e:  # noqa: BLE001
                        results["feed"][sym]["mt5_archive_error"] = str(e)

        # 4. live subscription availability (2 samples then forget)
        if flips:
            sym = flips[0]["symbol"]
            ws.send(json.dumps({"ticks": sym, "subscribe": 1, "req_id": 99999}))
            got, samples = time.time() + 15, []
            while time.time() < got and len(samples) < 2:
                try:
                    msg = json.loads(ws.recv())
                except Exception:  # noqa: BLE001
                    break
                if msg.get("msg_type") == "tick" and msg.get("subscription", {}).get("id"):
                    samples.append({"time": msg["tick"]["epoch"], "quote": msg["tick"]["quote"]})
            results["live_stream"] = {"available": len(samples) > 0, "n_samples": len(samples)}
            ws.send(json.dumps({"forget_all": "ticks"}))
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass

    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)

    # ---- printed report ----
    print("=" * 78)
    print("DERIV WEBSOCKET API RECON (read-only, app_id=1089)")
    print("=" * 78)
    p = results.get("ping", {})
    print(f"ping: {'ok' if not p.get('_error') else p['_error']}")
    print(f"authorize: {results.get('authorize_ok')} "
          f"{results.get('authorize_info', results.get('authorize_error', results.get('authorize_note', '')))}")
    print(f"active_symbols: {results.get('n_symbols_total')} symbols "
          f"(ok={results.get('active_symbols_ok')})")
    print(f"flip symbols found: {len(results.get('flip_symbols', []))}")
    for s in results.get("flip_symbols", []):
        open_f = "OPEN" if s.get("exchange_is_open") else "CLOSED"
        print(f"  - {s.get('symbol'):14s} {s.get('display_name'):34s} [{open_f}]")
    print()
    for sym, c in results.get("contracts", {}).items():
        print(f"contracts_for {sym}: MULTUP/MULTDOWN available = "
              f"{c.get('has_multup_multdown')} ({c.get('n_contracts')} contracts)")
        for cc in (c.get("contracts") or [])[:4]:
            if (cc or {}).get("contract_type") in ("MULTUP", "MULTDOWN"):
                print(f"   {cc['contract_type']:9s} stake {cc.get('min_stake_raw')}-"
                      f"{cc.get('max_stake')} mult {cc.get('multiplier_range')} "
                      f"commission {cc.get('commission')} stop_out {cc.get('stop_out_level')}")
                print(f"              cancellation {cc.get('cancellation_range')} "
                      f"limit_order keys {cc.get('limit_order')}")
    print()
    for sym, f in results.get("feed", {}).items():
        if "error" in f:
            print(f"ticks_history {sym}: ERROR {f['error']}")
            continue
        print(f"ticks_history {sym}: n={f['n_ticks']} window {f['t0_utc']} -> {f['tN_utc']} UTC")
        print(f"   last={f['last_price']}  median|d|={f['median_abs_delta']:.5f}  "
              f"spikes(5x)={f['spikes_5x']}  rate={f['tick_rate_per_min']}/min")
        mt = f.get("mt5_archive")
        if mt:
            print(f"   MT5 archive [{mt['file']}]: median|d|={mt['median_abs_delta']:.5f}  "
                  f"spike rate MT5={mt['spike_rate_pct']}% vs API={mt['api_spike_rate_pct']}%")
    print(f"live stream: {results.get('live_stream')}")
    print(f"\nfull JSON -> {OUT}")


if __name__ == "__main__":
    main()
