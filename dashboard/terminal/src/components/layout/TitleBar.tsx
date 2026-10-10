// ============================================================
// TitleBar — Symbol tabs, connection status, clock, layout toggle
// ============================================================

import { useState, useEffect, useRef } from 'react'
import { useStore, SYMBOL_CONFIG, ALL_SYMBOLS } from '../../store'
import { formatPrice } from '../../utils/format'
import { SYMBOL_COLORS } from '../../utils/colors'
import type { LayoutMode } from '../../types'

const LAYOUT_OPTIONS: { mode: LayoutMode; icon: string; label: string }[] = [
  { mode: 'single', icon: '▣', label: '1' },
  { mode: 'dual', icon: '▥', label: '2' },
  { mode: 'triple-left', icon: '▦', label: '3L' },
  { mode: 'triple-right', icon: '▧', label: '3R' },
  { mode: 'quad', icon: '⊞', label: '4' },
]

export function TitleBar() {
  const activeSymbol = useStore((s) => s.activeSymbol)
  const setActiveSymbol = useStore((s) => s.setActiveSymbol)
  const symbols = useStore((s) => s.symbols)
  const connected = useStore((s) => s.connected)
  const layoutMode = useStore((s) => s.layoutMode)
  const setLayoutMode = useStore((s) => s.setLayoutMode)
  const [clock, setClock] = useState('--:--:--')
  const prevPrices = useRef<Record<string, number>>({})

  useEffect(() => {
    const interval = setInterval(() => {
      setClock(new Date().toLocaleTimeString('en-US', { hour12: false }))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  const totalTrades = ALL_SYMBOLS.reduce((sum, s) => sum + (symbols[s]?.trades || 0), 0)
  const totalPnl = ALL_SYMBOLS.reduce((sum, s) => sum + (symbols[s]?.pnl || 0), 0)
  const totalWins = ALL_SYMBOLS.reduce((sum, s) => sum + (symbols[s]?.wins || 0), 0)
  const wr = totalTrades > 0 ? ((totalWins / totalTrades) * 100).toFixed(0) : '0'

  return (
    <div className="titlebar">
      {/* Logo */}
      <div className="logo">
        ⚡ GENESIS
        {connected ? (
          <span className="live-dot" />
        ) : (
          <span className="offline-dot" />
        )}
        <span style={{
          fontSize: 9,
          color: connected ? 'var(--green)' : 'var(--red)',
          letterSpacing: 0.5,
        }}>
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>

      {/* Symbol Tabs */}
      {ALL_SYMBOLS.map((sym) => {
        const cfg = SYMBOL_CONFIG[sym]
        const state = symbols[sym]
        const isActive = sym === activeSymbol
        const pnl = state?.pnl || 0
        const color = SYMBOL_COLORS[sym] || 'var(--accent)'
        const price = state?.price || 0
        const prevPrice = prevPrices.current[sym] || 0
        const priceClass = price > prevPrice ? 'price-up' : price < prevPrice ? 'price-down' : ''

        if (price !== prevPrice) {
          prevPrices.current[sym] = price
        }

        return (
          <div
            key={sym}
            className={`tab ${isActive ? 'active' : ''}`}
            onClick={() => setActiveSymbol(sym)}
            style={isActive ? { borderBottomColor: color } : undefined}
          >
            <span style={{ fontWeight: 700, color }}>
              {cfg.short}
            </span>
            <span className={`font-mono ${priceClass}`} style={{
              fontWeight: 600,
              fontSize: 11,
            }}>
              {price ? formatPrice(price, sym) : '--'}
            </span>
            <span style={{
              fontSize: 8,
              padding: '1px 5px',
              borderRadius: 'var(--radius-sm)',
              fontWeight: 600,
              background: pnl >= 0 ? 'var(--green-dim)' : 'var(--red-dim)',
              color: pnl >= 0 ? 'var(--green)' : 'var(--red)',
              transition: 'all var(--transition-fast)',
            }}>
              {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
            </span>
          </div>
        )
      })}

      {/* Spacer */}
      <div style={{ flex: 1 }} />

      {/* Layout Toggle */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 2,
        marginRight: 12,
        background: 'var(--bg3)',
        borderRadius: 'var(--radius-sm)',
        padding: '2px 3px',
      }}>
        {LAYOUT_OPTIONS.map((opt) => (
          <button
            key={opt.mode}
            onClick={() => setLayoutMode(opt.mode)}
            title={opt.label}
            style={{
              width: 22,
              height: 20,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              border: 'none',
              borderRadius: 3,
              background: layoutMode === opt.mode ? 'var(--accent)' : 'transparent',
              color: layoutMode === opt.mode ? '#fff' : 'var(--text-muted)',
              fontSize: 10,
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
              fontWeight: layoutMode === opt.mode ? 700 : 400,
            }}
            onMouseEnter={(e) => {
              if (layoutMode !== opt.mode) {
                e.currentTarget.style.background = 'var(--bg4)'
                e.currentTarget.style.color = 'var(--text)'
              }
            }}
            onMouseLeave={(e) => {
              if (layoutMode !== opt.mode) {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = 'var(--text-muted)'
              }
            }}
          >
            {opt.icon}
          </button>
        ))}
      </div>

      {/* Stats */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 14,
        fontSize: 10,
        color: 'var(--text-dim)',
      }}>
        <span>
          <span className="text-muted">Trades </span>
          <span className="font-mono" style={{ fontWeight: 600 }}>{totalTrades}</span>
        </span>
        <span>
          <span className="text-muted">PnL </span>
          <span className="font-mono" style={{
            fontWeight: 600,
            color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)',
          }}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
          </span>
        </span>
        <span>
          <span className="text-muted">WR </span>
          <span className="font-mono" style={{ fontWeight: 600 }}>{wr}%</span>
        </span>
        <div className={`dot ${connected ? 'connected' : 'disconnected'}`} />
        <span className="font-mono" style={{ fontWeight: 600, fontSize: 11 }}>
          {clock}
        </span>
      </div>
    </div>
  )
}
