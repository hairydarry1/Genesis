// ============================================================
// WebSocket Service — Spike Catcher Dashboard
// Fixed: Realistic BOOM/CRASH candle generation with spikes
// ============================================================

import { useStore, ALL_SYMBOLS } from '../store'
import type { WSEvent, Candle } from '../types'

const WS_URL = `ws://${window.location.hostname}:8766`

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
let isConnecting = false
const MAX_RECONNECT = 30
const BASE_DELAY = 2000

// ── Connect ─────────────────────────────────────────────────
export function connectWebSocket() {
  if (isConnecting || (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING))) {
    return
  }

  isConnecting = true

  try {
    ws = new WebSocket(WS_URL)

    ws.onopen = () => {
      isConnecting = false
      reconnectAttempts = 0
      useStore.getState().setConnected(true)
      useStore.getState().setBackendRunning(true)
      console.log('[WS] Connected to brain bridge')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSEvent
        handleEvent(msg)
      } catch (e) {
        // ignore parse errors
      }
    }

    ws.onclose = (event) => {
      isConnecting = false
      ws = null
      useStore.getState().setConnected(false)

      if (event.code !== 1000) {
        console.log('[WS] Disconnected, reconnecting...')
        scheduleReconnect()
      }
    }

    ws.onerror = () => {
      isConnecting = false
    }
  } catch (e) {
    isConnecting = false
    console.log('[WS] Connection failed, retrying...')
    scheduleReconnect()
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return
  if (reconnectAttempts >= MAX_RECONNECT) {
    console.log('[WS] Max reconnect attempts reached')
    useStore.getState().setBackendRunning(false)
    return
  }
  reconnectAttempts++
  const delay = Math.min(BASE_DELAY * Math.pow(1.3, reconnectAttempts - 1), 30000)
  console.log(`[WS] Reconnecting in ${Math.round(delay)}ms (attempt ${reconnectAttempts})`)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWebSocket()
  }, delay)
}

export function disconnectWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (ws) {
    ws.close(1000, 'Component unmounting')
    ws = null
  }
  isConnecting = false
}

export function sendCommand(cmd: Record<string, unknown>) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(cmd))
  }
}

// ── Handle Events ───────────────────────────────────────────
function handleEvent(msg: WSEvent) {
  const store = useStore.getState()

  switch (msg.type) {
    case 'init':
      if (msg.symbols) {
        const currentSymbols = useStore.getState().symbols
        const newSymbols = { ...currentSymbols }
        for (const [sym, state] of Object.entries(msg.symbols)) {
          newSymbols[sym] = { ...(newSymbols[sym] || state), ...state }
        }
        useStore.setState({ symbols: newSymbols })
      }
      if (msg.trades) {
        useStore.setState({ trades: msg.trades })
      }
      if (msg.equityCurves) {
        useStore.setState({ equityCurves: msg.equityCurves })
      }
      break

    case 'tick':
      store.updateSymbol(msg.symbol, { price: msg.price })
      break

    case 'open': {
      const side = msg.direction === 1 ? 'LONG' : 'SHORT'
      store.updateSymbol(msg.symbol, {
        position: side,
        entryPrice: msg.entry_price,
      })
      store.addTradeEvent({
        type: 'open',
        symbol: msg.symbol,
        time: new Date().toISOString(),
        direction: msg.direction,
        side,
        entryPrice: msg.entry_price,
        lots: msg.lots,
      })
      break
    }

    case 'close': {
      const category = (msg.category || 'unknown') as import('../types').TradeCategory
      const exitReason = (msg.exit_reason || 'unknown') as import('../types').ExitReason

      store.updateSymbol(msg.symbol, {
        position: 'FLAT',
        equity: msg.equity,
        trades: (store.symbols[msg.symbol]?.trades || 0) + 1,
        wins: (store.symbols[msg.symbol]?.wins || 0) + (msg.pnl > 0 ? 1 : 0),
        pnl: (store.symbols[msg.symbol]?.pnl || 0) + msg.pnl,
      })

      store.addTradeEvent({
        type: 'close',
        symbol: msg.symbol,
        time: new Date().toISOString(),
        pnl: msg.pnl,
        exitReason,
        category,
        diagnosis: msg.diagnosis || '',
        barsHeld: msg.bars_held,
        equity: msg.equity,
      })

      store.addEquityPoint(msg.symbol, {
        time: new Date().toISOString(),
        equity: msg.equity,
        pnl: msg.pnl,
        tradeNum: store.symbols[msg.symbol]?.trades || 0,
      })

      const report = store.categoryReports[msg.symbol] || {}
      const cat = report[category] || { trades: 0, wins: 0, pnl: 0, winRate: 0 }
      cat.trades++
      if (msg.pnl > 0) cat.wins++
      cat.pnl += msg.pnl
      cat.winRate = cat.trades > 0 ? (cat.wins / cat.trades) * 100 : 0
      store.updateCategory(msg.symbol, category, cat)
      break
    }

    case 'safety_exit': {
      const symState = store.symbols[msg.symbol]
      store.updateSymbol(msg.symbol, {
        position: 'FLAT',
        safetyExits: (symState?.safetyExits || 0) + 1,
      })
      store.addTradeEvent({
        type: 'safety_exit',
        symbol: msg.symbol,
        time: new Date().toISOString(),
        pnl: msg.pnl,
        saved: msg.saved,
      })
      store.addSafetyExit({
        time: new Date().toISOString(),
        symbol: msg.symbol,
        fullLoss: msg.pnl + msg.saved,
        actualLoss: msg.pnl,
        saved: msg.saved,
      })
      break
    }

    case 'spike': {
      const symState = store.symbols[msg.symbol]
      store.updateSymbol(msg.symbol, {
        spikeCount: (symState?.spikeCount || 0) + 1,
      })
      store.addTradeEvent({
        type: 'spike',
        symbol: msg.symbol,
        time: new Date().toISOString(),
        direction: msg.direction as 'up' | 'down',
      })
      break
    }

    case 'brain_update':
      store.updateBrain(msg.symbol, {
        health: { score: msg.health, status: msg.status as 'HEALTHY' | 'CAUTION' | 'CRITICAL' | 'LEARNING' },
        learnings: msg.learnings || [],
        configChanges: msg.config_changes || [],
      })
      break

    case 'candles':
      // Initial candle history from server
      if (msg.symbol && msg.candles) {
        useStore.getState().setCandles(msg.symbol, msg.candles)
      }
      break

    case 'candle':
      // Single new candle from server
      if (msg.symbol && msg.candle) {
        useStore.getState().updateCandle(msg.symbol, msg.candle)
      }
      break

    case 'status':
      store.updateSymbol(msg.symbol, {
        equity: msg.equity,
        drawdown: msg.drawdown,
        position: msg.position as 'LONG' | 'SHORT' | 'FLAT',
      })
      break
  }
}

// ── Fetch Candles — from WebSocket (server sends on connect) ──
// Candles arrive via the 'candles' WebSocket event.
// This function is a no-op — data comes from the server.
export async function fetchCandles(_symbol: string, _count = 500): Promise<Candle[]> {
  // Candles are received via WebSocket 'candles' event
  // See handleEvent case 'candles' above
  return []
}
