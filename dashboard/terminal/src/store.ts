// ============================================================
// Global State Store (Zustand)
// ============================================================

import { create } from 'zustand';
import type {
  Candle,
  Timeframe,
  IndicatorConfig,
  DrawingTool,
  Alert,
  ServerData,
  IndexMetrics,
  PortfolioSummary,
  WatchlistItem,
  Panel,
  PanelType,
  OrderBook,
  Trade,
  WorldModelPrediction,
  Tick,
} from './types';

// ----------------------------------------------------------------
// Symbol Config
// ----------------------------------------------------------------
export const SYMBOL_CONFIG: Record<string, { name: string; short: string; color: string; basePrice: number }> = {
  R_75: { name: 'Volatility 75', short: 'V75', color: '#58a6ff', basePrice: 52200 },
  R_10: { name: 'Volatility 10', short: 'V10', color: '#3fb950', basePrice: 4766 },
  BOOM500: { name: 'Boom 500', short: 'BOOM', color: '#d29922', basePrice: 4750 },
  CRASH500: { name: 'Crash 500', short: 'CRASH', color: '#f85149', basePrice: 2980 },
};

export const ALL_SYMBOLS = Object.keys(SYMBOL_CONFIG);

// ----------------------------------------------------------------
// Default Indicators
// ----------------------------------------------------------------
const DEFAULT_INDICATORS: IndicatorConfig[] = [
  { type: 'ema', enabled: true, params: { period: 9 }, color: '#58a6ff' },
  { type: 'ema', enabled: true, params: { period: 21 }, color: '#d29922' },
  { type: 'bb', enabled: false, params: { period: 20, stdDev: 2 }, color: '#bc8cff' },
  { type: 'rsi', enabled: false, params: { period: 14 }, color: '#58a6ff' },
  { type: 'macd', enabled: false, params: { fast: 12, slow: 26, signal: 9 } },
  { type: 'vwap', enabled: false, params: {}, color: '#3fb950' },
  { type: 'atr', enabled: false, params: { period: 14 }, color: '#db6d28' },
  { type: 'stoch', enabled: false, params: { k: 14, d: 3 }, color: '#58a6ff' },
  { type: 'adx', enabled: false, params: { period: 14 }, color: '#bc8cff' },
  { type: 'obv', enabled: false, params: {} },
  { type: 'cci', enabled: false, params: { period: 20 }, color: '#58a6ff' },
  { type: 'williams', enabled: false, params: { period: 14 }, color: '#3fb950' },
  { type: 'supertrend', enabled: false, params: { period: 10, multiplier: 3 }, color: '#58a6ff' },
  { type: 'psar', enabled: false, params: { step: 0.02, max: 0.2 }, color: '#d29922' },
];

// ----------------------------------------------------------------
// Store Interface
// ----------------------------------------------------------------
interface TerminalState {
  // Active symbol
  activeSymbol: string;
  setActiveSymbol: (s: string) => void;

  // Timeframe
  timeframe: Timeframe;
  setTimeframe: (tf: Timeframe) => void;

  // Candle data per symbol
  candleData: Record<string, Candle[]>;
  setCandles: (symbol: string, candles: Candle[]) => void;
  updateCandle: (symbol: string, candle: Candle) => void;

  // Indicators
  indicators: IndicatorConfig[];
  toggleIndicator: (type: string) => void;
  updateIndicatorParams: (type: string, params: Record<string, number>) => void;

  // Sub-charts
  showVolume: boolean;
  showRSI: boolean;
  showMACD: boolean;
  toggleSubChart: (chart: 'volume' | 'rsi' | 'macd') => void;

  // Drawing tools
  drawings: DrawingTool[];
  addDrawing: (d: DrawingTool) => void;
  removeDrawing: (id: string) => void;
  clearDrawings: () => void;
  activeDrawingTool: string | null;
  setActiveDrawingTool: (tool: string | null) => void;

  // Server data
  serverData: ServerData | null;
  setServerData: (data: ServerData) => void;

  // Per-index metrics
  indices: Record<string, IndexMetrics>;
  setIndices: (indices: Record<string, IndexMetrics>) => void;

  // Portfolio
  portfolio: PortfolioSummary;
  setPortfolio: (p: PortfolioSummary) => void;

  // Ticks
  ticks: Record<string, Tick[]>;
  addTick: (symbol: string, tick: Tick) => void;

  // Alerts
  alerts: Alert[];
  addAlert: (a: Alert) => void;

  // Trades
  trades: Trade[];
  addTrade: (t: Trade) => void;

  // Watchlist
  watchlist: WatchlistItem[];
  addToWatchlist: (item: WatchlistItem) => void;
  removeFromWatchlist: (symbol: string) => void;

  // World model predictions per symbol
  predictions: Record<string, WorldModelPrediction>;
  setPrediction: (symbol: string, pred: WorldModelPrediction) => void;

  // Order book
  orderBooks: Record<string, OrderBook>;
  setOrderBook: (symbol: string, ob: OrderBook) => void;

  // Panels
  panels: Panel[];
  addPanel: (type: PanelType, symbol: string) => void;
  removePanel: (id: string) => void;

  // WebSocket connection state
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;

  // Layout mode
  layout: 'single' | 'dual' | 'quad';
  setLayout: (l: 'single' | 'dual' | 'quad') => void;

  // Crosshair data
  crosshairData: Record<string, { time: number; ohlc: Candle }>;
  setCrosshairData: (symbol: string, data: { time: number; ohlc: Candle }) => void;

  // Log
  logEntries: string[];
  addLog: (msg: string) => void;
}

// ----------------------------------------------------------------
// Store
// ----------------------------------------------------------------
export const useStore = create<TerminalState>((set, get) => ({
  // Active symbol
  activeSymbol: 'R_75',
  setActiveSymbol: (s) => set({ activeSymbol: s }),

  // Timeframe
  timeframe: '1m',
  setTimeframe: (tf) => set({ timeframe: tf }),

  // Candle data
  candleData: {},
  setCandles: (symbol, candles) =>
    set((s) => ({ candleData: { ...s.candleData, [symbol]: candles } })),
  updateCandle: (symbol, candle) =>
    set((s) => {
      const candles = s.candleData[symbol] || [];
      const last = candles[candles.length - 1];
      let newCandles: Candle[];
      if (last && last.time === candle.time) {
        newCandles = [...candles.slice(0, -1), candle];
      } else {
        newCandles = [...candles, candle];
        if (newCandles.length > 2000) newCandles = newCandles.slice(-2000);
      }
      return { candleData: { ...s.candleData, [symbol]: newCandles } };
    }),

  // Indicators
  indicators: DEFAULT_INDICATORS,
  toggleIndicator: (type) =>
    set((s) => ({
      indicators: s.indicators.map((ind) =>
        ind.type === type ? { ...ind, enabled: !ind.enabled } : ind
      ),
    })),
  updateIndicatorParams: (type, params) =>
    set((s) => ({
      indicators: s.indicators.map((ind) =>
        ind.type === type ? { ...ind, params: { ...ind.params, ...params } } : ind
      ),
    })),

  // Sub-charts
  showVolume: true,
  showRSI: false,
  showMACD: false,
  toggleSubChart: (chart) =>
    set((s) => {
      if (chart === 'volume') return { showVolume: !s.showVolume };
      if (chart === 'rsi') return { showRSI: !s.showRSI };
      return { showMACD: !s.showMACD };
    }),

  // Drawing tools
  drawings: [],
  addDrawing: (d) => set((s) => ({ drawings: [...s.drawings, d] })),
  removeDrawing: (id) => set((s) => ({ drawings: s.drawings.filter((d) => d.id !== id) })),
  clearDrawings: () => set({ drawings: [] }),
  activeDrawingTool: null,
  setActiveDrawingTool: (tool) => set({ activeDrawingTool: tool }),

  // Server data
  serverData: null,
  setServerData: (data) => set({ serverData: data }),

  // Per-index metrics
  indices: {},
  setIndices: (indices) => set({ indices }),

  // Portfolio
  portfolio: {
    totalEquity: 40000,
    totalPnl: 0,
    portfolioDrawdown: 0,
    diversificationScore: 0,
    activePositions: 0,
    totalTrades: 0,
    overallWinRate: 0,
    kelly: 0.25,
  },
  setPortfolio: (p) => set({ portfolio: p }),

  // Ticks
  ticks: {},
  addTick: (symbol, tick) =>
    set((s) => {
      const ticks = s.ticks[symbol] || [];
      const newTicks = [...ticks, tick];
      if (newTicks.length > 5000) newTicks.splice(0, newTicks.length - 5000);
      return { ticks: { ...s.ticks, [symbol]: newTicks } };
    }),

  // Alerts
  alerts: [],
  addAlert: (a) => set((s) => ({ alerts: [a, ...s.alerts].slice(0, 200) })),

  // Trades
  trades: [],
  addTrade: (t) => set((s) => ({ trades: [t, ...s.trades].slice(0, 500) })),

  // Watchlist
  watchlist: ALL_SYMBOLS.map((sym) => ({
    symbol: sym,
    name: SYMBOL_CONFIG[sym].name,
    lastPrice: SYMBOL_CONFIG[sym].basePrice,
    change: 0,
    changePercent: 0,
    volume: 0,
    addedAt: Date.now(),
  })),
  addToWatchlist: (item) =>
    set((s) => ({
      watchlist: [...s.watchlist.filter((w) => w.symbol !== item.symbol), item],
    })),
  removeFromWatchlist: (symbol) =>
    set((s) => ({ watchlist: s.watchlist.filter((w) => w.symbol !== symbol) })),

  // World model predictions
  predictions: {},
  setPrediction: (symbol, pred) =>
    set((s) => ({ predictions: { ...s.predictions, [symbol]: pred } })),

  // Order book
  orderBooks: {},
  setOrderBook: (symbol, ob) =>
    set((s) => ({ orderBooks: { ...s.orderBooks, [symbol]: ob } })),

  // Panels
  panels: [
    { id: 'chart-1', type: 'chart', symbol: 'R_75', title: 'V75 Chart', size: 100 },
  ],
  addPanel: (type, symbol) =>
    set((s) => ({
      panels: [
        ...s.panels,
        { id: `${type}-${Date.now()}`, type, symbol, title: `${SYMBOL_CONFIG[symbol]?.short || symbol} ${type}`, size: 50 },
      ],
    })),
  removePanel: (id) => set((s) => ({ panels: s.panels.filter((p) => p.id !== id) })),

  // WebSocket connection
  wsConnected: false,
  setWsConnected: (connected) => set({ wsConnected: connected }),

  // Layout
  layout: 'single',
  setLayout: (l) => set({ layout: l }),

  // Crosshair
  crosshairData: {},
  setCrosshairData: (symbol, data) =>
    set((s) => ({ crosshairData: { ...s.crosshairData, [symbol]: data } })),

  // Log
  logEntries: ['[INIT] Genesis Trading Terminal v2.0'],
  addLog: (msg) =>
    set((s) => ({
      logEntries: [`[${new Date().toLocaleTimeString()}] ${msg}`, ...s.logEntries].slice(0, 500),
    })),
}));
