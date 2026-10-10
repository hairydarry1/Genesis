// ============================================================
// SymbolPanel — Per-symbol price, equity, WR, position
// ============================================================

import { useRef, useEffect } from 'react'
import { useStore, SYMBOL_CONFIG, ALL_SYMBOLS } from '../../store'
import { formatPrice, formatMoney, formatPercent } from '../../utils/format'
import { SYMBOL_COLORS } from '../../utils/colors'

export function SymbolPanel() {
  const activeSymbol = useStore((s) => s.activeSymbol)
  const setActiveSymbol = useStore((s) => s.setActiveSymbol)
  const symbols = useStore((s) => s.symbols)
  const connected = useStore((s) => s.connected)

  return (
    <div className="panel">
      <div className="panel-header">
        <span>📊</span> Market Watch
        {!connected && (
          <span className="spinner sm" style={{ marginLeft: 'auto' }} />
        )}
      </div>
      <div className="panel-body" style={{ padding: 0 }}>
        {ALL_SYMBOLS.map((sym) => (
          <SymbolCard
            key={sym}
            symbol={sym}
            isActive={sym === activeSymbol}
            onClick={() => setActiveSymbol(sym)}
          />
        ))}
      </div>
    </div>
  )
}

// ── Individual Symbol Card ──────────────────────────────────
function SymbolCard({ symbol, isActive, onClick }: {
  symbol: string
  isActive: boolean
  onClick: () => void
}) {
  const state = useStore((s) => s.symbols[symbol])
  const cfg = SYMBOL_CONFIG[symbol]
  const color = SYMBOL_COLORS[symbol] || 'var(--accent)'
  const wr = state?.trades > 0 ? ((state.wins / state.trades) * 100).toFixed(0) : '0'
  const priceRef = useRef<HTMLSpanElement>(null)
  const prevPriceRef = useRef(0)

  // Price flash animation
  useEffect(() => {
    const price = state?.price || 0
    if (price !== prevPriceRef.current && priceRef.current && prevPriceRef.current > 0) {
      const el = priceRef.current
      el.classList.remove('price-up', 'price-down')
      void el.offsetWidth // force reflow
      el.classList.add(price > prevPriceRef.current ? 'price-up' : 'price-down')
    }
    prevPriceRef.current = price
  }, [state?.price])

  return (
    <div
      onClick={onClick}
      style={{
        padding: '8px 10px',
        borderBottom: '1px solid rgba(31,41,55,0.5)',
        cursor: 'pointer',
        background: isActive ? 'var(--bg3)' : 'transparent',
        borderLeft: isActive ? `2px solid ${color}` : '2px solid transparent',
        transition: 'all var(--transition-fast)',
      }}
    >
      {/* Symbol Name + Price */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 3 }}>
        <span style={{ fontWeight: 700, fontSize: 11, color }}>{cfg.short}</span>
        <span
          ref={priceRef}
          className="font-mono"
          style={{ fontWeight: 600, fontSize: 11 }}
        >
          {state?.price ? formatPrice(state.price, symbol) : (
            <span className="skeleton skeleton-text short" style={{ display: 'inline-block', width: 50 }} />
          )}
        </span>
      </div>

      {/* Stats Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, fontSize: 9 }}>
        <StatRow label="Equity" value={`$${(state?.equity || 10000).toFixed(0)}`} />
        <StatRow
          label="Return"
          value={formatPercent(state?.returnPct || 0)}
          color={(state?.returnPct || 0) >= 0 ? 'var(--green)' : 'var(--red)'}
        />
        <StatRow label="Trades" value={String(state?.trades || 0)} />
        <StatRow label="WR" value={`${wr}%`} />
        <StatRow
          label="PnL"
          value={formatMoney(state?.pnl || 0)}
          color={(state?.pnl || 0) >= 0 ? 'var(--green)' : 'var(--red)'}
        />
        <StatRow label="DD" value={`${(state?.drawdown || 0).toFixed(2)}%`} />
      </div>

      {/* Position Badge */}
      <div style={{ marginTop: 4, display: 'flex', gap: 4, alignItems: 'center' }}>
        <span className={`badge ${(state?.position || 'FLAT').toLowerCase()}`}>
          {state?.position || 'FLAT'}
        </span>
        {state?.position !== 'FLAT' && state?.entryPrice && (
          <span style={{ fontSize: 8, color: 'var(--text-muted)' }}>
            @ {formatPrice(state.entryPrice, symbol)}
          </span>
        )}
        <span className={`badge ${state?.volRegime || 'calm'}`} style={{ marginLeft: 'auto' }}>
          {state?.volRegime || 'calm'}
        </span>
      </div>
    </div>
  )
}

// ── Stat Row ────────────────────────────────────────────────
function StatRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ color: 'var(--text-dim)' }}>
      {label}{' '}
      <span style={{ color: color || 'var(--text)', fontWeight: 600 }}>
        {value}
      </span>
    </div>
  )
}
