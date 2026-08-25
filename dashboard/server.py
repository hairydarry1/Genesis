"""
dashboard/server.py — HTTP + WebSocket server for Genesis trading dashboard.
Serves live candlestick data, portfolio state, and backtest results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import websockets
    import websockets.server
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class DashboardData:
    def __init__(self, data_dir: str = "checkpoints_tiny"):
        self.data_dir = Path(data_dir)
        self._cache: dict = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 1.0
        self._env_cache: dict = {}
        self._policy_cache: dict = {}
        self._candle_cache: dict = {}  # symbol -> { candles: [...], time: float }
        self._candle_cache_ttl: float = 30.0  # Refresh every 30s

    def get_data(self) -> dict:
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        data = {
            'portfolio': self._get_portfolio_summary(),
            'indices': self._get_index_metrics(),
            'correlation_matrix': self._get_correlation_matrix(),
            'optimizer': self._get_optimizer_data(),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self._cache = data
        self._cache_time = now
        return data

    def _get_portfolio_summary(self) -> dict:
        state_file = self.data_dir / "portfolio_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    return json.load(f).get('summary', self._default_portfolio())
            except Exception:
                pass
        return self._default_portfolio()

    def _default_portfolio(self) -> dict:
        return {
            'total_equity': 40000.0, 'total_pnl': 0.0,
            'portfolio_drawdown': 0.0, 'diversification_score': 0.0,
            'active_positions': 0, 'total_trades': 0,
            'overall_win_rate': 0.0, 'index_count': 4, 'alerts': [],
        }

    def _get_index_metrics(self) -> dict:
        indices = {}
        for idx in ['R_75', 'R_10', 'BOOM500', 'CRASH500']:
            for path in [self.data_dir / f"monitor_{idx.lower()}.json", self.data_dir / "portfolio_state.json"]:
                if path.exists():
                    try:
                        with open(path) as f:
                            data = json.load(f)
                        key = 'indices' if 'indices' in data else 'metrics'
                        if key in data and idx in data[key]:
                            indices[idx] = data[key][idx]
                            break
                    except Exception:
                        pass
            if idx not in indices:
                indices[idx] = self._default_idx(idx)
        return indices

    def _default_idx(self, name: str) -> dict:
        return {
            'name': name, 'equity': 10000.0, 'position': 0,
            'total_pnl': 0.0, 'trade_count': 0, 'win_rate': 0.0,
            'max_drawdown': 0.0, 'current_drawdown': 0.0,
            'sharpe_ratio': 0.0, 'sortino_ratio': 0.0, 'profit_factor': 0.0,
            'wm_direction': 'FLAT', 'wm_confidence': 0.0,
            'wm_mean': 0.0, 'wm_std': 0.0,
        }

    def _get_correlation_matrix(self) -> dict:
        state_file = self.data_dir / "portfolio_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    return json.load(f).get('correlation_matrix', self._default_corr())
            except Exception:
                pass
        return self._default_corr()

    def _default_corr(self) -> dict:
        indices = ['R_75', 'R_10', 'BOOM500', 'CRASH500']
        return {i1: {i2: 1.0 if i1 == i2 else 0.0 for i2 in indices} for i1 in indices}

    def _get_optimizer_data(self) -> dict:
        state_file = self.data_dir / "portfolio_state.json"
        if state_file.exists():
            try:
                with open(state_file) as f:
                    return json.load(f).get('optimizer', {})
            except Exception:
                pass
        return {}

    def get_candles(self, symbol: str, count: int = 500) -> list[dict]:
        """Fetch candles from Deriv API with caching."""
        now = time.time()
        cache = self._candle_cache.get(symbol)
        if cache and now - cache['time'] < self._candle_cache_ttl:
            return cache['candles']

        try:
            import asyncio
            candles = asyncio.get_event_loop().run_until_complete(
                self._fetch_deriv_candles(symbol, count)
            )
            if candles:
                self._candle_cache[symbol] = {'candles': candles, 'time': now}
                return candles
        except Exception:
            pass

        # Fallback: generate synthetic candles
        return self._generate_synthetic_candles(symbol, count)

    async def _fetch_deriv_candles(self, symbol: str, count: int = 500) -> list[dict]:
        """Fetch real candles from Deriv websocket API."""
        import websockets as ws_lib

        url = "wss://ws.derivws.com/websockets/v3?app_id=1089"
        async with ws_lib.connect(url) as ws:
            # Request candles
            req = {
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "count": min(count, 1000),
                "end": "latest",
                "style": "candles",
                "granularity": 60,
            }
            await ws.send(json.dumps(req))
            resp = json.loads(await ws.recv())

            candles = []
            for c in resp.get("candles", []):
                candles.append({
                    "time": int(c["epoch"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            return candles

    def _generate_synthetic_candles(self, symbol: str, count: int = 500) -> list[dict]:
        """Generate realistic synthetic candles as fallback."""
        import random
        base_prices = {
            'R_75': 52200, 'R_10': 4766, 'BOOM500': 4750, 'CRASH500': 2980,
        }
        price = base_prices.get(symbol, 10000)
        candles = []
        now = int(time.time()) - count * 60

        for i in range(count):
            vol = price * 0.001
            open_p = price
            change = (random.random() - 0.48) * vol
            close_p = open_p + change
            high_p = max(open_p, close_p) + random.random() * vol * 0.5
            low_p = min(open_p, close_p) - random.random() * vol * 0.5

            candles.append({
                "time": now,
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "volume": round(random.random() * 1000 + 100),
            })
            price = close_p
            now += 60

        return candles

    def run_backtest(self, index: str, start: str, end: str, equity: float) -> dict:
        """Run a backtest simulation."""
        import numpy as np
        try:
            from env.trading_env import TradingEnv, TradingEnvConfig
            from model.policy import PolicyNetwork, PolicyConfig
            from model.train_tiny import TINY_FEATURES
            import torch
            import torch.nn.functional as F

            dataset_map = {
                'R_75': 'data/dataset_R75_h10.npz',
                'R_10': 'data/dataset_volatility_10_h10.npz',
                'BOOM500': 'data/dataset_boom_500_h10.npz',
                'CRASH500': 'data/dataset_crash_500_h10.npz',
            }
            ckpt_map = {
                'R_75': 'checkpoints_tiny/policy_best.pt',
                'R_10': 'checkpoints_vol10/policy_best.pt',
                'BOOM500': 'checkpoints_boom_500/policy_best.pt',
                'CRASH500': 'checkpoints_crash_500/policy_best.pt',
            }

            if index not in dataset_map:
                return {'error': f'Unknown index: {index}'}

            env_key = f"{index}_{equity}"
            if env_key not in self._env_cache:
                self._env_cache[env_key] = TradingEnv(
                    dataset_map[index], tiny_features=TINY_FEATURES,
                    config=TradingEnvConfig(max_steps=500, initial_equity=equity),
                )
            env = self._env_cache[env_key]

            if index not in self._policy_cache:
                ckpt = torch.load(ckpt_map[index], map_location='cpu', weights_only=False)
                policy = PolicyNetwork(PolicyConfig(obs_dim=11, n_actions=3))
                policy.load_state_dict(ckpt['policy_state_dict'])
                policy.eval()
                self._policy_cache[index] = policy
            policy = self._policy_cache[index]

            obs = env.reset()
            equity_curve = [equity]
            drawdown_curve = [0.0]
            trades = []
            peak = equity
            in_position = False
            entry_price = 0.0
            entry_step = 0

            for step in range(min(2000, env.n_samples - 2)):
                obs_tensor = torch.tensor(obs, dtype=torch.float32)
                with torch.no_grad():
                    logits, _, _ = policy(obs_tensor.unsqueeze(0))
                    action = int(F.softmax(logits, dim=-1).argmax().item())
                obs, reward, done, info = env.step(action)

                eq = info.get('equity', equity)
                equity_curve.append(eq)
                peak = max(peak, eq)
                dd = (peak - eq) / peak if peak > 0 else 0
                drawdown_curve.append(-dd)

                pos = info.get('position', 0)
                if pos != 0 and not in_position:
                    in_position = True
                    entry_price = info.get('entry_price', 0)
                    entry_step = step
                elif pos == 0 and in_position:
                    in_position = False
                    exit_price = info.get('current_price', entry_price)
                    pnl = eq - equity_curve[-2]
                    trades.append({
                        'time': f'Step {entry_step}', 'side': 'LONG',
                        'entry': entry_price, 'exit': exit_price,
                        'pnl': pnl, 'holding': step - entry_step,
                    })

                if done:
                    break

            final_eq = equity_curve[-1]
            total_return = (final_eq / equity - 1) * 100
            returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
            sharpe = float(np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252 * 24 * 60)) if len(returns) > 10 else 0
            max_dd = abs(min(drawdown_curve)) if drawdown_curve else 0
            wins = sum(1 for t in trades if t['pnl'] > 0)
            win_rate = wins / len(trades) if trades else 0
            gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
            gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
            pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

            return {
                'total_return': total_return,
                'sharpe_ratio': sharpe,
                'max_drawdown': max_dd,
                'win_rate': win_rate,
                'profit_factor': min(pf, 10.0),
                'equity_curve': equity_curve[-500:],
                'drawdown_curve': drawdown_curve[-500:],
                'trades': trades[-50:],
            }
        except Exception as e:
            return {
                'error': str(e), 'total_return': 0, 'sharpe_ratio': 0,
                'max_drawdown': 0, 'win_rate': 0, 'profit_factor': 0,
                'equity_curve': [], 'drawdown_curve': [], 'trades': [],
            }


class DashboardHandler(BaseHTTPRequestHandler):
    dashboard_data = None
    _directory = str(Path(__file__).parent)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == '/api/data':
                self._json_response(self.dashboard_data.get_data())
            elif parsed.path == '/api/candles':
                params = parse_qs(parsed.query)
                symbol = params.get('symbol', ['R_75'])[0]
                count = int(params.get('count', ['500'])[0])
                candles = self.dashboard_data.get_candles(symbol, count)
                self._json_response({'candles': candles})
            elif parsed.path == '/api/backtest':
                params = parse_qs(parsed.query)
                index = params.get('index', ['R_75'])[0]
                start = params.get('start', ['2026-02-24'])[0]
                end = params.get('end', ['2026-08-24'])[0]
                equity = float(params.get('equity', ['10000'])[0])
                result = self.dashboard_data.run_backtest(index, start, end, equity)
                self._json_response(result)
            elif parsed.path == '/' or parsed.path == '/index.html':
                self._serve_file(Path(__file__).parent / 'index.html', 'text/html')
            else:
                file_path = Path(self._directory) / parsed.path.lstrip('/')
                if file_path.exists() and file_path.is_file():
                    self._serve_file(file_path, 'text/html')
                else:
                    self.send_error(404, 'Not Found')
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response({'error': str(e)})

    def do_HEAD(self):
        self.do_GET()

    def _json_response(self, data):
        resp = json.dumps(data, default=str)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(resp.encode())

    def _serve_file(self, path, content_type):
        if path.exists():
            with open(path, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


class WebSocketServer:
    def __init__(self, data: DashboardData, host: str = "localhost", ws_port: int = 8081):
        self.data = data
        self.host = host
        self.ws_port = ws_port
        self._clients = set()

    async def handler(self, websocket):
        self._clients.add(websocket)
        try:
            data = self.data.get_data()
            await websocket.send(json.dumps(data, default=str))
            async for _ in websocket:
                pass
        finally:
            self._clients.discard(websocket)

    async def broadcast(self):
        while True:
            if self._clients:
                data = self.data.get_data()
                msg = json.dumps(data, default=str)
                disconnected = set()
                for client in self._clients:
                    try:
                        await client.send(msg)
                    except Exception:
                        disconnected.add(client)
                self._clients -= disconnected
            await asyncio.sleep(2)

    async def run(self):
        if not HAS_WEBSOCKETS:
            return
        async with websockets.serve(self.handler, self.host, self.ws_port):
            await self.broadcast()


def run_server(port: int = 8080, ws_port: int = 8081, data_dir: str = "checkpoints_tiny"):
    import asyncio

    data = DashboardData(data_dir)
    DashboardHandler.dashboard_data = data

    http_server = ThreadingHTTPServer(('localhost', port), DashboardHandler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()

    print(f"\n{'='*60}")
    print(f"  Genesis Trading Terminal")
    print(f"{'='*60}")
    print(f"  HTTP:      http://localhost:{port}")
    if HAS_WEBSOCKETS:
        print(f"  WebSocket: ws://localhost:{ws_port}")
    print(f"  Data:      {data_dir}")
    print(f"{'='*60}\n")

    if HAS_WEBSOCKETS:
        ws_server = WebSocketServer(data, ws_port=ws_port)
        try:
            asyncio.run(ws_server.run())
        except KeyboardInterrupt:
            pass
    else:
        print("Websockets not installed. Using polling mode.")
        print("Install with: pip install websockets")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    http_server.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Genesis Trading Terminal")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--ws-port", type=int, default=8081)
    parser.add_argument("--data-dir", default="checkpoints_tiny")
    args = parser.parse_args()
    run_server(args.port, args.ws_port, args.data_dir)


if __name__ == "__main__":
    main()
