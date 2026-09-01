"""
eval/websocket_server.py — WebSocket server for real-time dashboard updates.

Broadcasts portfolio state, trade events, and risk alerts to connected clients.
Replaces polling with push-based updates.

Usage:
    python eval.websocket_server.py --port 8081
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Set

try:
    import websockets
    import websockets.server
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""
    
    def __init__(self, port: int = 8081):
        self.port = port
        self._clients: Set = set()
        self._data_provider = None
        self._running = False
    
    def set_data_provider(self, provider):
        """Set the data provider for portfolio updates."""
        self._data_provider = provider
    
    async def handler(self, websocket):
        """Handle a new WebSocket connection."""
        self._clients.add(websocket)
        try:
            # Send initial state
            if self._data_provider:
                data = self._data_provider()
                await websocket.send(json.dumps(data, default=str))
            
            # Keep connection alive and handle messages
            async for message in websocket:
                try:
                    msg = json.loads(message)
                    # Handle client requests (e.g., subscribe to specific index)
                    if msg.get('type') == 'subscribe':
                        pass  # Future: filter updates
                except json.JSONDecodeError:
                    pass
        finally:
            self._clients.discard(websocket)
    
    async def broadcast(self, data: dict):
        """Broadcast data to all connected clients."""
        if not self._clients:
            return
        
        message = json.dumps(data, default=str)
        disconnected = set()
        
        for client in self._clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                disconnected.add(client)
        
        self._clients -= disconnected
    
    async def broadcast_event(self, event_type: str, data: dict):
        """Broadcast a specific event (trade, alert, etc.)."""
        event = {
            'type': event_type,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'data': data,
        }
        await self.broadcast(event)
    
    async def run(self):
        """Run the WebSocket server."""
        if not HAS_WEBSOCKETS:
            print("Websockets not installed. Install with: pip install websockets")
            return
        
        self._running = True
        
        async with websockets.serve(self.handler, "localhost", self.port):
            print(f"WebSocket server running on ws://localhost:{self.port}")
            
            # Periodic broadcast
            while self._running:
                if self._clients and self._data_provider:
                    data = self._data_provider()
                    await self.broadcast(data)
                await asyncio.sleep(2)  # Broadcast every 2 seconds
    
    def stop(self):
        """Stop the server."""
        self._running = False


class TradeEventBroadcaster:
    """Broadcasts trade events to WebSocket clients."""
    
    def __init__(self, ws_manager: WebSocketManager):
        self.ws = ws_manager
    
    async def on_trade_open(self, index: str, side: str, price: float, equity: float):
        await self.ws.broadcast_event('trade_open', {
            'index': index,
            'side': side,
            'price': price,
            'equity': equity,
        })
    
    async def on_trade_close(self, index: str, side: str, entry: float, exit: float, pnl: float):
        await self.ws.broadcast_event('trade_close', {
            'index': index,
            'side': side,
            'entry': entry,
            'exit': exit,
            'pnl': pnl,
        })
    
    async def on_risk_alert(self, level: str, index: str, message: str):
        await self.ws.broadcast_event('risk_alert', {
            'level': level,
            'index': index,
            'message': message,
        })
    
    async def on_model_update(self, index: str, direction: str, confidence: float):
        await self.ws.broadcast_event('model_update', {
            'index': index,
            'direction': direction,
            'confidence': confidence,
        })


async def main():
    """Run standalone WebSocket server."""
    import sys
    sys.path.insert(0, '.')
    from dashboard.server import DashboardData
    
    data = DashboardData('checkpoints_tiny')
    ws = WebSocketManager(port=8081)
    ws.set_data_provider(data.get_data)
    
    print("Starting WebSocket server...")
    await ws.run()


if __name__ == "__main__":
    asyncio.run(main())
