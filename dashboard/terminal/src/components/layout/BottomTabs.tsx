// ============================================================
// BottomTabs — Trade feed, post-mortem, safety, brain, control
// ============================================================

import { useStore, ALL_SYMBOLS } from '../../store'
import type { BottomTab as TabType } from '../../types'
import { formatTime, formatMoney } from '../../utils/format'
import { CATEGORY_COLORS } from '../../utils/colors'

const TABS: { id: TabType; label: string; icon: string }[] = [
  { id: 'trades', label: 'TRADES', icon: '📋' },
  { id: 'postmortem', label: 'POST-MORTEM', icon: '🔍' },
  { id: 'safety', label: 'SAFETY', icon: '🛡️' },
  { id: 'brain', label: 'BRAIN LOG', icon: '🧠' },
  { id: 'control', label: 'CONTROL', icon: '⚙️' },
]

export function BottomTabs() {
  const bottomTab = useStore((s) => s.bottomTab)
  const setBottomTab = useStore((s) => s.setBottomTab)
  const tradeEvents = useStore((s) => s.tradeEvents)
  const safetyExits = useStore((s) => s.safetyExits)
  const categoryReports = useStore((s) => s.categoryReports)
  const activeSymbol = useStore((s) => s.activeSymbol)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
      {/* Tab Bar */}
      <div className="bottom-tabs">
        {TABS.map((tab) => (
          <div
            key={tab.id}
            className={`bottom-tab ${bottomTab === tab.id ? 'active' : ''}`}
            onClick={() => setBottomTab(tab.id)}
          >
            <span style={{ marginRight: 4 }}>{tab.icon}</span>
            {tab.label}
            {tab.id === 'trades' && tradeEvents.length > 0 && (
              <span style={{
                marginLeft: 4,
                fontSize: 7,
                background: 'var(--accent-dim)',
                color: 'var(--accent)',
                padding: '0 4px',
                borderRadius: 8,
                fontWeight: 700,
              }}>
                {tradeEvents.length}
              </span>
            )}
            {tab.id === 'safety' && safetyExits.length > 0 && (
              <span style={{
                marginLeft: 4,
                fontSize: 7,
                background: 'var(--yellow-dim)',
                color: 'var(--yellow)',
                padding: '0 4px',
                borderRadius: 8,
                fontWeight: 700,
              }}>
                {safetyExits.length}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Tab Content */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {bottomTab === 'trades' && <TradeFeedTab events={tradeEvents} />}
        {bottomTab === 'postmortem' && <PostMortemTab reports={categoryReports} activeSymbol={activeSymbol} />}
        {bottomTab === 'safety' && <SafetyTab exits={safetyExits} />}
        {bottomTab === 'brain' && <BrainLogTab events={tradeEvents} />}
        {bottomTab === 'control' && (
          <div className="empty-state">
            <div className="icon">⚙️</div>
            <div className="title">Control Panel</div>
            <div className="subtitle">Use the control panel on the right to manage trading</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Trade Feed Tab ──────────────────────────────────────────
function TradeFeedTab({ events }: { events: import('../../types').TradeEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="empty-state">
        <div className="icon">📋</div>
        <div className="title">No trades yet</div>
        <div className="subtitle">Trade events will appear here in real-time</div>
      </div>
    )
  }

  return (
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Time</th>
          <th>Symbol</th>
          <th>Event</th>
          <th>P&L</th>
          <th>Detail</th>
        </tr>
      </thead>
      <tbody>
        {events.slice(0, 50).map((e, i) => (
          <tr key={`${e.time}-${i}`} className={i === 0 ? 'new-row' : ''}>
            <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
            <td style={{ color: 'var(--text-muted)', fontSize: 9 }}>{formatTime(e.time)}</td>
            <td>
              <span className="font-bold" style={{ color: 'var(--accent)' }}>
                {e.symbol?.replace('500', '').replace('1000', '')}
              </span>
            </td>
            <td>
              <EventBadge event={e} />
            </td>
            <td style={{
              fontWeight: 600,
              fontFamily: 'var(--mono)',
              color: (e.pnl || 0) > 0 ? 'var(--green)' : (e.pnl || 0) < 0 ? 'var(--red)' : 'var(--text-muted)',
            }}>
              {e.pnl !== undefined ? formatMoney(e.pnl) : '---'}
            </td>
            <td style={{ fontSize: 9, color: 'var(--text-muted)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {e.category && (
                <span className={`cat-badge ${e.category}`} style={{ marginRight: 4 }}>
                  {e.category}
                </span>
              )}
              {e.diagnosis || ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Event Badge ─────────────────────────────────────────────
function EventBadge({ event }: { event: import('../../types').TradeEvent }) {
  if (event.type === 'open') {
    return (
      <span style={{
        color: 'var(--text)',
        fontWeight: 600,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
      }}>
        <span style={{ fontSize: 10 }}>
          {event.direction === 1 ? '🟢' : '🔴'}
        </span>
        {event.side} @ {(event.entryPrice || 0).toFixed(2)}
      </span>
    )
  }

  if (event.type === 'safety_exit') {
    return (
      <span style={{ color: 'var(--yellow)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ fontSize: 10 }}>⚡</span>
        SAFETY EXIT
      </span>
    )
  }

  if (event.type === 'spike') {
    return (
      <span style={{ color: 'var(--orange)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ fontSize: 10 }}>▼</span>
        SPIKE
      </span>
    )
  }

  // close
  const isWin = (event.pnl || 0) > 0
  return (
    <span style={{
      color: isWin ? 'var(--green)' : 'var(--red)',
      fontWeight: 600,
      display: 'flex',
      alignItems: 'center',
      gap: 4,
    }}>
      <span style={{ fontSize: 10 }}>{isWin ? '✅' : '❌'}</span>
      {(event.exitReason || 'exit').toUpperCase()} {event.barsHeld || 0}b
    </span>
  )
}

// ── Post-Mortem Tab ─────────────────────────────────────────
function PostMortemTab({ reports, activeSymbol }: { reports: Record<string, Record<string, import('../../types').CategoryStats>>; activeSymbol: string }) {
  const report = reports[activeSymbol] || {}
  const categories = Object.entries(report).sort((a, b) => b[1].pnl - a[1].pnl)

  if (categories.length === 0) {
    return (
      <div className="empty-state">
        <div className="icon">🔍</div>
        <div className="title">No post-mortem data yet</div>
        <div className="subtitle">Trade categories will appear after a few trades</div>
      </div>
    )
  }

  return (
    <div style={{ padding: 8 }}>
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th style={{ textAlign: 'right' }}>Trades</th>
            <th style={{ textAlign: 'right' }}>WR</th>
            <th style={{ textAlign: 'right' }}>P&L</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {categories.map(([cat, stats], i) => (
            <tr key={cat} style={{ animation: `fadeInUp ${0.1 + i * 0.05}s ease` }}>
              <td>
                <span className={`cat-badge ${cat}`}>{cat}</span>
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'var(--mono)' }}>{stats.trades}</td>
              <td style={{
                textAlign: 'right',
                fontFamily: 'var(--mono)',
                fontWeight: 600,
                color: stats.winRate >= 55 ? 'var(--green)' : stats.winRate < 45 ? 'var(--red)' : 'var(--text)',
              }}>
                {stats.winRate.toFixed(0)}%
              </td>
              <td style={{
                textAlign: 'right',
                fontFamily: 'var(--mono)',
                fontWeight: 600,
                color: stats.pnl >= 0 ? 'var(--green)' : 'var(--red)',
              }}>
                {formatMoney(stats.pnl)}
              </td>
              <td>
                <StatusBadge pnl={stats.pnl} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Status Badge ────────────────────────────────────────────
function StatusBadge({ pnl }: { pnl: number }) {
  if (pnl > 0) {
    return <span style={{ fontSize: 8, color: 'var(--green)' }}>✅ KEEP</span>
  }
  if (pnl < -0.5) {
    return <span style={{ fontSize: 8, color: 'var(--red)' }}>❌ SKIP</span>
  }
  return <span style={{ fontSize: 8, color: 'var(--yellow)' }}>⚠️ WATCH</span>
}

// ── Safety Tab ──────────────────────────────────────────────
function SafetyTab({ exits }: { exits: import('../../types').SafetyExit[] }) {
  const totalSaved = exits.reduce((sum, e) => sum + e.saved, 0)

  if (exits.length === 0) {
    return (
      <div className="empty-state">
        <div className="icon">🛡️</div>
        <div className="title">No safety exits yet</div>
        <div className="subtitle">Safety exits will appear when spikes are detected during trades</div>
      </div>
    )
  }

  return (
    <div style={{ padding: 8 }}>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Time</th>
            <th>Symbol</th>
            <th style={{ textAlign: 'right' }}>Full Loss</th>
            <th style={{ textAlign: 'right' }}>Actual</th>
            <th style={{ textAlign: 'right' }}>Saved</th>
          </tr>
        </thead>
        <tbody>
          {exits.slice(0, 30).map((e, i) => (
            <tr key={i} style={{ animation: `fadeInUp ${0.1 + i * 0.03}s ease` }}>
              <td style={{ color: 'var(--text-muted)' }}>{i + 1}</td>
              <td style={{ color: 'var(--text-muted)', fontSize: 9 }}>{formatTime(e.time)}</td>
              <td>
                <span className="font-bold" style={{ color: 'var(--yellow)' }}>
                  {e.symbol?.replace('500', '').replace('1000', '')}
                </span>
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--red)' }}>
                {formatMoney(e.fullLoss)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--orange)' }}>
                {formatMoney(e.actualLoss)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--green)', fontWeight: 600 }}>
                {formatMoney(e.saved)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Summary */}
      <div className="card safety" style={{
        marginTop: 8,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
      }}>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
          🛡️ {exits.length} safety exits
        </span>
        <span style={{ color: 'var(--green)', fontWeight: 700, fontFamily: 'var(--mono)' }}>
          Saved: {formatMoney(totalSaved)}
        </span>
      </div>
    </div>
  )
}

// ── Brain Log Tab ───────────────────────────────────────────
function BrainLogTab({ events }: { events: import('../../types').TradeEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="empty-state">
        <div className="icon">🧠</div>
        <div className="title">No brain decisions yet</div>
        <div className="subtitle">Brain decisions will appear as trades are analyzed</div>
      </div>
    )
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Decision</th>
          <th>Symbol</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {events.slice(0, 50).map((e, i) => (
          <tr key={i} style={{ animation: i === 0 ? 'fadeInUp 0.2s ease' : undefined }}>
            <td style={{ color: 'var(--text-muted)', fontSize: 9 }}>{formatTime(e.time)}</td>
            <td>
              <span style={{
                color: e.type === 'open' ? 'var(--accent)' : e.type === 'close' ? 'var(--text-dim)' : 'var(--yellow)',
                fontWeight: 600,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
              }}>
                <span style={{ fontSize: 10 }}>
                  {e.type === 'open' ? '🟢' : e.type === 'close' ? '⚪' : e.type === 'safety_exit' ? '⚡' : '▼'}
                </span>
                {e.type === 'open' ? 'ENTER' : e.type === 'close' ? 'EXIT' : e.type === 'safety_exit' ? 'SAFETY' : 'SPIKE'}
              </span>
            </td>
            <td>
              <span className="font-bold" style={{ color: 'var(--accent)' }}>
                {e.symbol?.replace('500', '').replace('1000', '')}
              </span>
            </td>
            <td style={{ fontSize: 9, color: 'var(--text-muted)', maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {e.category && (
                <span className={`cat-badge ${e.category}`} style={{ marginRight: 4 }}>
                  {e.category}
                </span>
              )}
              {e.diagnosis || e.exitReason || ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
