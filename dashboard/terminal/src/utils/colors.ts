// ============================================================
// Color Constants — Dark Trading Theme
// ============================================================

export const COLORS = {
  // Background
  bg: '#0a0e17',
  bg2: '#111827',
  bg3: '#1f2937',
  bg4: '#374151',

  // Borders
  border: '#1f2937',
  borderLight: '#374151',

  // Text
  text: '#e5e7eb',
  textDim: '#9ca3af',
  textMuted: '#6b7280',

  // Accent
  accent: '#3b82f6',
  accentDim: 'rgba(59, 130, 246, 0.15)',

  // Semantic
  green: '#10b981',
  greenDim: 'rgba(16, 185, 129, 0.15)',
  red: '#ef4444',
  redDim: 'rgba(239, 68, 68, 0.15)',
  yellow: '#f59e0b',
  yellowDim: 'rgba(245, 158, 11, 0.15)',
  purple: '#8b5cf6',
  purpleDim: 'rgba(139, 92, 246, 0.15)',
  orange: '#f97316',
  orangeDim: 'rgba(249, 115, 22, 0.15)',
  cyan: '#06b6d4',
  cyanDim: 'rgba(6, 182, 212, 0.15)',
} as const

// Symbol-specific colors
export const SYMBOL_COLORS: Record<string, string> = {
  BOOM500: '#3b82f6',
  CRASH500: '#10b981',
  BOOM1000: '#f59e0b',
  CRASH1000: '#8b5cf6',
  CRASH300: '#06b6d4',
  BOOM300: '#f97316',
}

// Category colors
export const CATEGORY_COLORS: Record<string, string> = {
  good_fade: COLORS.green,
  late_entry: COLORS.red,
  algo_spike_trap: COLORS.orange,
  cluster_continuation: COLORS.purple,
  noise_fade: COLORS.cyan,
  trend_against: COLORS.red,
  safety_spike: COLORS.yellow,
  unknown: COLORS.textMuted,
}

// Exit reason colors
export const EXIT_COLORS: Record<string, string> = {
  tp: COLORS.green,
  sl: COLORS.red,
  timeout: COLORS.yellow,
  safety_spike: COLORS.orange,
  force_close: COLORS.red,
}

// Health status colors
export function healthColor(score: number): string {
  if (score >= 0.7) return COLORS.green
  if (score >= 0.4) return COLORS.yellow
  return COLORS.red
}

// PnL color
export function pnlColor(pnl: number): string {
  if (pnl > 0) return COLORS.green
  if (pnl < 0) return COLORS.red
  return COLORS.textMuted
}
