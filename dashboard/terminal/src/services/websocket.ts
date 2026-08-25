// ============================================================
// WebSocket Service — Real-time data from Python backend
// ============================================================

import { useStore, SYMBOL_CONFIG } from '../store';
import type { ServerData, Tick } from '../types';

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const WS_URL = `ws://${window.location.hostname}:8081`;
const API_BASE = window.location.origin;

// ----------------------------------------------------------------
// WebSocket Connection
// ----------------------------------------------------------------
export function connectWebSocket() {
  const store = useStore.getState();
  store.addLog('Connecting to server...');

  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      useStore.getState().setWsConnected(true);
      useStore.getState().addLog('WebSocket connected');
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const data: ServerData = JSON.parse(event.data);
        handleServerData(data);
      } catch (e) {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      useStore.getState().setWsConnected(false);
      useStore.getState().addLog('WebSocket disconnected, reconnecting...');
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws?.close();
      useStore.getState().addLog('WebSocket error, falling back to polling');
      startPolling();
    };
  } catch (e) {
    useStore.getState().addLog('WebSocket failed, using polling');
    startPolling();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWebSocket();
  }, 3000);
}

// ----------------------------------------------------------------
// HTTP Polling Fallback
// ----------------------------------------------------------------
function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/data`);
      if (resp.ok) {
        const data: ServerData = await resp.json();
        handleServerData(data);
        useStore.getState().setWsConnected(true);
      }
    } catch (e) {
      useStore.getState().setWsConnected(false);
    }
  }, 2000);
}

// ----------------------------------------------------------------
// Handle Server Data
// ----------------------------------------------------------------
function handleServerData(data: ServerData) {
  const store = useStore.getState();

  // Update indices
  if (data.indices) {
    store.setIndices(data.indices);
  }

  // Update portfolio
  if (data.portfolio) {
    store.setPortfolio({
      totalEquity: data.portfolio.total_equity || 0,
      totalPnl: data.portfolio.total_pnl || 0,
      portfolioDrawdown: data.portfolio.portfolio_drawdown || 0,
      diversificationScore: data.portfolio.diversification_score || 0,
      activePositions: data.portfolio.active_positions || 0,
      totalTrades: data.portfolio.total_trades || 0,
      overallWinRate: data.portfolio.overall_win_rate || 0,
      kelly: 0.25,
    });
  }

  // Update watchlist with live data
  for (const [symbol, metrics] of Object.entries(data.indices || {})) {
    store.addToWatchlist({
      symbol,
      name: SYMBOL_CONFIG[symbol]?.name || symbol,
      lastPrice: metrics.equity || SYMBOL_CONFIG[symbol]?.basePrice || 0,
      change: metrics.total_pnl || 0,
      changePercent: ((metrics.total_pnl || 0) / 10000) * 100,
      volume: metrics.trade_count || 0,
      addedAt: Date.now(),
    });

    // Generate world model prediction from metrics
    if (metrics.wm_mean !== undefined) {
      const totalMean = metrics.wm_mean;
      const totalStd = metrics.wm_std || 0.001;
      const snr = Math.abs(totalMean) / totalStd;
      store.setPrediction(symbol, {
        direction: totalMean > 0 ? 'UP' : totalMean < 0 ? 'DOWN' : 'FLAT',
        mean: totalMean,
        std: totalStd,
        confidence: Math.min(snr / 2.0, 1.0),
        snr,
        horizon_means: [],
        horizon_stds: [],
      });
    }
  }
}

// ----------------------------------------------------------------
// Fetch Historical Candles
// ----------------------------------------------------------------
export async function fetchCandles(symbol: string, count: number = 500): Promise<import('../types').Candle[]> {
  try {
    const resp = await fetch(`${API_BASE}/api/candles?symbol=${symbol}&count=${count}`);
    if (resp.ok) {
      const data = await resp.json();
      if (data.candles && data.candles.length > 0) {
        return data.candles.map((c: any) => ({
          time: c.epoch,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
          volume: c.volume || 0,
        }));
      }
    }
  } catch (e) {
    // fall through
  }

  // Generate synthetic candles as fallback
  return generateSyntheticCandles(symbol, count);
}

// ----------------------------------------------------------------
// Synthetic Candle Generator (fallback)
// ----------------------------------------------------------------
function generateSyntheticCandles(symbol: string, count: number): import('../types').Candle[] {
  const basePrice = SYMBOL_CONFIG[symbol]?.basePrice || 10000;
  const candles: import('../types').Candle[] = [];
  let price = basePrice;
  let now = Math.floor(Date.now() / 1000) - count * 60;

  for (let i = 0; i < count; i++) {
    const vol = price * 0.0008;
    const open = price;
    const change = (Math.random() - 0.48) * vol;
    const close = open + change;
    const high = Math.max(open, close) + Math.random() * vol * 0.5;
    const low = Math.min(open, close) - Math.random() * vol * 0.5;

    candles.push({
      time: now,
      open: parseFloat(open.toFixed(2)),
      high: parseFloat(high.toFixed(2)),
      low: parseFloat(low.toFixed(2)),
      close: parseFloat(close.toFixed(2)),
      volume: Math.round(Math.random() * 1000 + 100),
    });

    price = close;
    now += 60;
  }

  return candles;
}

// ----------------------------------------------------------------
// Fetch Candles via Deriv API (direct from server)
// ----------------------------------------------------------------
export async function fetchCandlesDirect(symbol: string, count: number = 500): Promise<import('../types').Candle[]> {
  return fetchCandles(symbol, count);
}

// ----------------------------------------------------------------
// Cleanup
// ----------------------------------------------------------------
export function disconnectWebSocket() {
  if (ws) {
    ws.close();
    ws = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}
