// ============================================================
// Core Types for Genesis Trading Terminal
// ============================================================

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Tick {
  quote: number;
  epoch: number;
  symbol: string;
}

export interface WorldModelPrediction {
  direction: 'UP' | 'DOWN' | 'FLAT';
  mean: number;
  std: number;
  confidence: number;
  snr: number;
  horizon_means: number[];
  horizon_stds: number[];
}

export interface Position {
  symbol: string;
  side: 'LONG' | 'SHORT' | 'FLAT';
  entryPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
  equity: number;
  peakEquity: number;
  drawdown: number;
  tradeCount: number;
  winCount: number;
  winRate: number;
}

export interface Trade {
  timestamp: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entry: number;
  exit: number;
  pnl: number;
  holding: number;
  win: boolean;
}

export interface PortfolioSummary {
  totalEquity: number;
  totalPnl: number;
  portfolioDrawdown: number;
  diversificationScore: number;
  activePositions: number;
  totalTrades: number;
  overallWinRate: number;
  kelly: number;
}

export interface IndexMetrics {
  name: string;
  equity: number;
  position: number;
  totalPnl: number;
  tradeCount: number;
  winRate: number;
  maxDrawdown: number;
  currentDrawdown: number;
  sharpeRatio: number;
  sortinoRatio: number;
  profitFactor: number;
  wmDirection: string;
  wmConfidence: number;
  wmMean: number;
  wmStd: number;
  positionSize: number;
}

export interface ServerData {
  indices: Record<string, IndexMetrics>;
  portfolio: PortfolioSummary;
  correlationMatrix: Record<string, Record<string, number>>;
  optimizer: Record<string, unknown>;
  timestamp: string;
}

export interface Alert {
  id: string;
  time: string;
  type: 'info' | 'warn' | 'danger' | 'success';
  message: string;
  symbol?: string;
}

export type Timeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';

export type IndicatorType = 'ema' | 'sma' | 'bb' | 'rsi' | 'macd' | 'vwap' | 'atr' | 'stoch' | 'adx' | 'obv' | 'cci' | 'williams' | 'ichimoku' | 'supertrend' | 'psar';

export interface IndicatorConfig {
  type: IndicatorType;
  enabled: boolean;
  params: Record<string, number>;
  color?: string;
}

export interface DrawingTool {
  type: 'trendline' | 'horizontal' | 'fibonacci' | 'rectangle' | 'arrow' | 'text';
  points: Array<{ time: number; price: number }>;
  color: string;
  id: string;
}

export interface OrderBookEntry {
  price: number;
  volume: number;
  total: number;
}

export interface OrderBook {
  bids: OrderBookEntry[];
  asks: OrderBookEntry[];
  spread: number;
  midPrice: number;
}

export interface WatchlistItem {
  symbol: string;
  name: string;
  lastPrice: number;
  change: number;
  changePercent: number;
  volume: number;
  addedAt: number;
}

export type PanelType = 
  | 'chart' 
  | 'orderbook' 
  | 'trades' 
  | 'portfolio' 
  | 'correlation' 
  | 'alerts' 
  | 'journal'
  | 'worldmodel'
  | 'heatmap';

export interface Panel {
  id: string;
  type: PanelType;
  symbol: string;
  title: string;
  size: number; // percentage
}

export interface KeyBinding {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  alt?: boolean;
  action: string;
  description: string;
}
