// ============================================================
// Formatting Utilities
// ============================================================

export function formatPrice(price: number, symbol: string): string {
  const digits = symbol.includes('1000') || symbol.includes('300') || symbol.includes('150') || symbol.includes('99') || symbol.includes('100') || symbol.includes('200') ? 4 : 3
  return price.toFixed(digits)
}

export function formatPnl(pnl: number): string {
  const sign = pnl >= 0 ? '+' : ''
  return `${sign}$${Math.abs(pnl).toFixed(4)}`
}

export function formatPnlShort(pnl: number): string {
  const sign = pnl >= 0 ? '+' : ''
  return `${sign}${pnl.toFixed(2)}`
}

export function formatPercent(pct: number): string {
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

export function formatTime(iso: string): string {
  if (!iso) return '--:--'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return '--:--'
  }
}

export function formatTimeShort(iso: string): string {
  if (!iso) return '--:--'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return '--:--'
  }
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

export function formatLotSize(lots: number): string {
  return lots.toFixed(2)
}

export function formatMoney(amount: number): string {
  const sign = amount >= 0 ? '+' : ''
  return `${sign}$${Math.abs(amount).toFixed(2)}`
}

export function clsx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(' ')
}
