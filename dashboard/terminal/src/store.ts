// ============================================================
// Spike Catcher — Zustand Store
// ============================================================

import { create } from 'zustand'
import type {
  SymbolState,
  Trade,
  TradeEvent,
  BrainState,
  EquityPoint,
  CategoryStats,
  SafetyExit,
  BottomTab,
  Candle,
  LayoutMode,
  LayoutCell,
} from './types'
import { LAYOUT_PRESETS } from './types'

// ── Symbol Config ───────────────────────────────────────────
export const SYMBOL_CONFIG: Record<string, { name: string; short: string; color: string; digits: number }> = {
  BOOM500: { name: 'Boom 500', short: 'BOOM', color: '#3b82f6', digits: 3 },
  CRASH500: { name: 'Crash 500', short: 'CRASH', color: '#10b981', digits: 3 },
  BOOM1000: { name: 'Boom 1000', short: 'BOOM1K', color: '#f59e0b', digits: 4 },
  CRASH1000: { name: 'Crash 1000', short: 'CRASH1K', color: '#8b5cf6', digits: 4 },
}

export const ALL_SYMBOLS = Object.keys(SYMBOL_CONFIG)

// ── Default Symbol State ────────────────────────────────────
function defaultSymbolState(symbol: string): SymbolState {
  return {
    symbol,
    price: 0,
    equity: 10000,
    returnPct: 0,
    drawdown: 0,
    trades: 0,
    wins: 0,
    pnl: 0,
    position: 'FLAT',
    entryPrice: 0,
    volRegime: 'calm',
    riskPct: 0.10,
    kelly: 0,
    lastTrade: null,
    spikeCount: 0,
    safetyExits: 0,
  }
}

// ── Default Brain State ─────────────────────────────────────
function defaultBrainState(): BrainState {
  return {
    health: { score: 0.5, status: 'LEARNING' },
    learnings: [],
    configChanges: [],
    stats: { tradesAnalyzed: 0, patternsFound: 0, adjustmentsMade: 0 },
    consecLosses: 0,
    maxConsecLosses: 0,
  }
}

// ── Store Interface ─────────────────────────────────────────
interface TradingStore {
  // Active symbol
  activeSymbol: string
  setActiveSymbol: (s: string) => void

  // Bottom tab
  bottomTab: BottomTab
  setBottomTab: (t: BottomTab) => void

  // Connection
  connected: boolean
  setConnected: (c: boolean) => void

  // Backend
  backendRunning: boolean
  setBackendRunning: (r: boolean) => void

  // Per-symbol state
  symbols: Record<string, SymbolState>
  updateSymbol: (symbol: string, partial: Partial<SymbolState>) => void
  setSymbols: (symbols: Record<string, SymbolState>) => void

  // Candle data per symbol
  candles: Record<string, Candle[]>
  setCandles: (symbol: string, candles: Candle[]) => void
  updateCandle: (symbol: string, candle: Candle) => void

  // Trades
  trades: Trade[]
  addTrade: (t: Trade) => void
  setTrades: (trades: Trade[]) => void

  // Trade events (real-time feed)
  tradeEvents: TradeEvent[]
  addTradeEvent: (e: TradeEvent) => void

  // Brain state per symbol
  brains: Record<string, BrainState>
  updateBrain: (symbol: string, partial: Partial<BrainState>) => void

  // Equity curves
  equityCurves: Record<string, EquityPoint[]>
  addEquityPoint: (symbol: string, point: EquityPoint) => void
  setEquityCurves: (curves: Record<string, EquityPoint[]>) => void

  // Category reports
  categoryReports: Record<string, Record<string, CategoryStats>>
  updateCategory: (symbol: string, category: string, stats: Partial<CategoryStats>) => void

  // Safety exits
  safetyExits: SafetyExit[]
  addSafetyExit: (e: SafetyExit) => void

  // Risk per symbol (override)
  riskOverrides: Record<string, number>
  setRiskOverride: (symbol: string, risk: number) => void

  // Trading active
  tradingActive: boolean
  setTradingActive: (a: boolean) => void

  // Layout
  layoutMode: LayoutMode
  setLayoutMode: (mode: LayoutMode) => void
  layoutCells: LayoutCell[]
  setLayoutCells: (cells: LayoutCell[]) => void
  updateLayoutCell: (id: string, symbol: string) => void
}

// ── Store ───────────────────────────────────────────────────
export const useStore = create<TradingStore>((set, get) => ({
  // Active symbol
  activeSymbol: 'BOOM500',
  setActiveSymbol: (s) => set({ activeSymbol: s }),

  // Bottom tab
  bottomTab: 'trades',
  setBottomTab: (t) => set({ bottomTab: t }),

  // Connection
  connected: false,
  setConnected: (c) => set({ connected: c }),

  // Backend
  backendRunning: false,
  setBackendRunning: (r) => set({ backendRunning: r }),

  // Per-symbol state
  symbols: Object.fromEntries(ALL_SYMBOLS.map((s) => [s, defaultSymbolState(s)])),
  updateSymbol: (symbol, partial) =>
    set((state) => ({
      symbols: {
        ...state.symbols,
        [symbol]: { ...(state.symbols[symbol] || defaultSymbolState(symbol)), ...partial },
      },
    })),
  setSymbols: (symbols) => set({ symbols }),

  // Candle data
  candles: {},
  setCandles: (symbol, candles) =>
    set((state) => ({ candles: { ...state.candles, [symbol]: candles } })),
  updateCandle: (symbol, candle) =>
    set((state) => {
      const existing = state.candles[symbol] || []
      const last = existing[existing.length - 1]
      let newCandles: Candle[]
      if (last && last.time === candle.time) {
        newCandles = [...existing.slice(0, -1), candle]
      } else {
        newCandles = [...existing, candle]
        if (newCandles.length > 2000) newCandles = newCandles.slice(-2000)
      }
      return { candles: { ...state.candles, [symbol]: newCandles } }
    }),

  // Trades
  trades: [],
  addTrade: (t) =>
    set((state) => ({ trades: [t, ...state.trades].slice(0, 500) })),
  setTrades: (trades) => set({ trades }),

  // Trade events
  tradeEvents: [],
  addTradeEvent: (e) =>
    set((state) => ({ tradeEvents: [e, ...state.tradeEvents].slice(0, 200) })),

  // Brain state
  brains: Object.fromEntries(ALL_SYMBOLS.map((s) => [s, defaultBrainState()])),
  updateBrain: (symbol, partial) =>
    set((state) => ({
      brains: {
        ...state.brains,
        [symbol]: { ...(state.brains[symbol] || defaultBrainState()), ...partial },
      },
    })),

  // Equity curves
  equityCurves: Object.fromEntries(ALL_SYMBOLS.map((s) => [s, []])),
  addEquityPoint: (symbol, point) =>
    set((state) => ({
      equityCurves: {
        ...state.equityCurves,
        [symbol]: [...(state.equityCurves[symbol] || []), point].slice(-500),
      },
    })),
  setEquityCurves: (curves) => set({ equityCurves: curves }),

  // Category reports
  categoryReports: {},
  updateCategory: (symbol, category, stats) =>
    set((state) => {
      const report = state.categoryReports[symbol] || {}
      const existing = report[category] || { trades: 0, wins: 0, pnl: 0, winRate: 0 }
      report[category] = { ...existing, ...stats }
      return { categoryReports: { ...state.categoryReports, [symbol]: { ...report } } }
    }),

  // Safety exits
  safetyExits: [],
  addSafetyExit: (e) =>
    set((state) => ({ safetyExits: [e, ...state.safetyExits].slice(0, 100) })),

  // Risk overrides
  riskOverrides: {},
  setRiskOverride: (symbol, risk) =>
    set((state) => ({ riskOverrides: { ...state.riskOverrides, [symbol]: risk } })),

  // Trading active
  tradingActive: true,
  setTradingActive: (a) => set({ tradingActive: a }),

  // Layout
  layoutMode: 'single',
  setLayoutMode: (mode) => set({
    layoutMode: mode,
    layoutCells: LAYOUT_PRESETS[mode].cells,
  }),
  layoutCells: LAYOUT_PRESETS.single.cells,
  setLayoutCells: (cells) => set({ layoutCells: cells }),
  updateLayoutCell: (id, symbol) =>
    set((state) => ({
      layoutCells: state.layoutCells.map((c) =>
        c.id === id ? { ...c, symbol } : c
      ),
    })),
}))
