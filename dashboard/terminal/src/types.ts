// ============================================================
// Spike Catcher — Type Definitions
// ============================================================

// ── Market Data ─────────────────────────────────────────────
export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Tick {
  price: number
  time: number
  symbol: string
}

export interface Spike {
  time: number
  symbol: string
  size: number
  direction: 'up' | 'down'
  barIndex: number
}

// ── Symbol State ────────────────────────────────────────────
export interface SymbolState {
  symbol: string
  price: number
  equity: number
  returnPct: number
  drawdown: number
  trades: number
  wins: number
  pnl: number
  position: 'LONG' | 'SHORT' | 'FLAT'
  entryPrice: number
  volRegime: 'calm' | 'volatile' | 'trending'
  riskPct: number
  kelly: number
  lastTrade: TradeEvent | null
  spikeCount: number
  safetyExits: number
}

// ── Trades ──────────────────────────────────────────────────
export type ExitReason = 'tp' | 'sl' | 'timeout' | 'safety_spike' | 'force_close'

export type TradeCategory =
  | 'good_fade'
  | 'late_entry'
  | 'algo_spike_trap'
  | 'cluster_continuation'
  | 'noise_fade'
  | 'trend_against'
  | 'safety_spike'
  | 'unknown'

export interface TradeEvent {
  type: 'open' | 'close' | 'safety_exit' | 'spike'
  symbol: string
  time: string
  direction?: 1 | -1
  side?: 'LONG' | 'SHORT'
  entryPrice?: number
  exitPrice?: number
  lots?: number
  pnl?: number
  pnlPct?: number
  exitReason?: ExitReason
  category?: TradeCategory
  diagnosis?: string
  barsHeld?: number
  equity?: number
  saved?: number // damage saved by safety exit
}

export interface Trade {
  id: number
  symbol: string
  time: string
  side: 'LONG' | 'SHORT'
  entry: number
  exit: number
  pnl: number
  barsHeld: number
  exitReason: ExitReason
  category: TradeCategory
  diagnosis: string
}

// ── Brain ───────────────────────────────────────────────────
export interface BrainHealth {
  score: number // 0-1
  status: 'HEALTHY' | 'CAUTION' | 'CRITICAL' | 'LEARNING'
}

export interface BrainLearning {
  time: string
  insight: string
  action: string
  impact: string
}

export interface ConfigChange {
  time: string
  parameter: string
  oldValue: number | string
  newValue: number | string
  reason: string
}

export interface BrainState {
  health: BrainHealth
  learnings: BrainLearning[]
  configChanges: ConfigChange[]
  stats: {
    tradesAnalyzed: number
    patternsFound: number
    adjustmentsMade: number
  }
  consecLosses: number
  maxConsecLosses: number
}

// ── Equity Curve ────────────────────────────────────────────
export interface EquityPoint {
  time: string
  equity: number
  pnl: number
  tradeNum: number
}

// ── Category Report ─────────────────────────────────────────
export interface CategoryStats {
  trades: number
  wins: number
  pnl: number
  winRate: number
}

// ── Safety Exit ─────────────────────────────────────────────
export interface SafetyExit {
  time: string
  symbol: string
  fullLoss: number
  actualLoss: number
  saved: number
}

// ── WebSocket Events ────────────────────────────────────────
export type WSEvent =
  | { type: 'init'; symbols: Record<string, SymbolState>; trades: Trade[]; equityCurves: Record<string, EquityPoint[]>; candles?: Record<string, Candle[]> }
  | { type: 'tick'; symbol: string; price: number }
  | { type: 'candles'; symbol: string; candles: Candle[] }
  | { type: 'candle'; symbol: string; candle: Candle }
  | { type: 'open'; symbol: string; direction: 1 | -1; entry_price: number; lots: number }
  | { type: 'close'; symbol: string; pnl: number; exit_reason: string; category: string; diagnosis: string; equity: number; bars_held: number }
  | { type: 'safety_exit'; symbol: string; pnl: number; saved: number }
  | { type: 'spike'; symbol: string; size: number; direction: string }
  | { type: 'brain_update'; symbol: string; health: number; status: string; learnings: BrainLearning[]; config_changes: ConfigChange[] }
  | { type: 'status'; symbol: string; equity: number; wr: number; trades: number; drawdown: number; position: string }

export type WSCommand =
  | { action: 'start'; symbols: string[] }
  | { action: 'stop' }
  | { action: 'set_risk'; symbol: string; risk: number }
  | { action: 'force_close'; symbol: string }

// ── Config ──────────────────────────────────────────────────
export interface SymbolConfig {
  name: string
  short: string
  color: string
  minLot: number
  digits: number
}

// ── UI State ────────────────────────────────────────────────
export type BottomTab = 'trades' | 'postmortem' | 'safety' | 'brain' | 'control'

// ── Layout ──────────────────────────────────────────────────
export type LayoutMode = 'single' | 'dual' | 'quad' | 'triple-left' | 'triple-right'

export interface LayoutCell {
  id: string
  symbol: string
  gridArea: string // CSS grid-area value
}

export interface LayoutPreset {
  name: string
  mode: LayoutMode
  cells: LayoutCell[]
}

export const LAYOUT_PRESETS: Record<LayoutMode, LayoutPreset> = {
  single: {
    name: 'Single',
    mode: 'single',
    cells: [
      { id: 'main', symbol: 'BOOM500', gridArea: '1 / 1 / 2 / 2' },
    ],
  },
  dual: {
    name: 'Dual',
    mode: 'dual',
    cells: [
      { id: 'left', symbol: 'BOOM500', gridArea: '1 / 1 / 2 / 2' },
      { id: 'right', symbol: 'CRASH500', gridArea: '1 / 2 / 2 / 3' },
    ],
  },
  quad: {
    name: 'Quad',
    mode: 'quad',
    cells: [
      { id: 'tl', symbol: 'BOOM500', gridArea: '1 / 1 / 2 / 2' },
      { id: 'tr', symbol: 'CRASH500', gridArea: '1 / 2 / 2 / 3' },
      { id: 'bl', symbol: 'BOOM1000', gridArea: '2 / 1 / 3 / 2' },
      { id: 'br', symbol: 'CRASH1000', gridArea: '2 / 2 / 3 / 3' },
    ],
  },
  'triple-left': {
    name: 'Triple L',
    mode: 'triple-left',
    cells: [
      { id: 'big', symbol: 'BOOM500', gridArea: '1 / 1 / 3 / 2' },
      { id: 'top', symbol: 'CRASH500', gridArea: '1 / 2 / 2 / 3' },
      { id: 'bot', symbol: 'BOOM1000', gridArea: '2 / 2 / 3 / 3' },
    ],
  },
  'triple-right': {
    name: 'Triple R',
    mode: 'triple-right',
    cells: [
      { id: 'top', symbol: 'BOOM500', gridArea: '1 / 1 / 2 / 2' },
      { id: 'bot', symbol: 'CRASH500', gridArea: '2 / 1 / 3 / 2' },
      { id: 'big', symbol: 'BOOM1000', gridArea: '1 / 2 / 3 / 3' },
    ],
  },
}

export interface AppState {
  activeSymbol: string
  bottomTab: BottomTab
  connected: boolean
  backendRunning: boolean
}
