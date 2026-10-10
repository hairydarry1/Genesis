// ============================================================
// BrainPanel — Brain health, learning log, config changes
// ============================================================

import { useStore } from '../../store'
import { healthColor } from '../../utils/colors'

export function BrainPanel() {
  const activeSymbol = useStore((s) => s.activeSymbol)
  const brain = useStore((s) => s.brains[activeSymbol])
  const connected = useStore((s) => s.connected)

  if (!brain) return <LoadingSkeleton />

  const { health, learnings, configChanges, stats, consecLosses } = brain
  const barColor = healthColor(health.score)

  return (
    <div className="panel" style={{ overflowY: 'auto' }}>
      <div className="panel-header">
        <span>🧠</span> Strategy Brain
        {!connected && <span className="spinner sm" style={{ marginLeft: 'auto' }} />}
      </div>
      <div className="panel-body">
        {/* Health Score */}
        <div style={{ textAlign: 'center', marginBottom: 10, animation: 'fadeIn 0.3s ease' }}>
          <div className="font-bold" style={{
            fontSize: 28,
            color: barColor,
            transition: 'color 0.5s ease',
            lineHeight: 1,
          }}>
            {(health.score * 100).toFixed(0)}%
          </div>
          <div style={{
            fontSize: 9,
            color: 'var(--text-dim)',
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            marginTop: 2,
          }}>
            {health.status}
          </div>
          <div className="health-bar" style={{ marginTop: 6 }}>
            <div className="fill" style={{
              width: `${health.score * 100}%`,
              background: barColor,
            }} />
          </div>
        </div>

        {/* Stats */}
        <div className="card brain" style={{ marginBottom: 8 }}>
          <div className="pr">
            <span className="l">Trades Analyzed</span>
            <span className="v">{stats.tradesAnalyzed}</span>
          </div>
          <div className="pr">
            <span className="l">Patterns Found</span>
            <span className="v text-accent">{stats.patternsFound}</span>
          </div>
          <div className="pr">
            <span className="l">Adjustments</span>
            <span className="v text-yellow">{stats.adjustmentsMade}</span>
          </div>
          <div className="pr">
            <span className="l">Consec Losses</span>
            <span className={`v ${consecLosses >= 3 ? 'neg' : ''}`}>{consecLosses}</span>
          </div>
        </div>

        {/* Recent Learnings */}
        {learnings.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{
              fontSize: 9,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              color: 'var(--text-muted)',
              marginBottom: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}>
              <span>💡</span> What It Learned
            </div>
            {learnings.slice(0, 3).map((l, i) => (
              <div
                key={i}
                className="card"
                style={{
                  marginBottom: 4,
                  animation: `fadeInUp ${0.2 + i * 0.1}s ease`,
                  borderLeftColor: 'var(--yellow)',
                }}
              >
                <div style={{ fontSize: 8, color: 'var(--yellow)', marginBottom: 2 }}>
                  {l.time}
                </div>
                <div style={{ fontSize: 9, color: 'var(--text)', fontStyle: 'italic', lineHeight: 1.4 }}>
                  "{l.insight}"
                </div>
                <div style={{ fontSize: 8, color: 'var(--text-muted)', marginTop: 2 }}>
                  → {l.action}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Config Changes */}
        {configChanges.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{
              fontSize: 9,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              color: 'var(--text-muted)',
              marginBottom: 6,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}>
              <span>⚙️</span> Config Changes
            </div>
            {configChanges.slice(0, 5).map((c, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '3px 6px',
                  fontSize: 9,
                  borderRadius: 'var(--radius-sm)',
                  animation: `slideInRight ${0.2 + i * 0.05}s ease`,
                }}
              >
                <span className="text-dim">{c.parameter}</span>
                <span className="font-mono">
                  <span className="text-muted">{String(c.oldValue)}</span>
                  <span className="text-dim" style={{ margin: '0 3px' }}>→</span>
                  <span className="text-accent font-bold">{String(c.newValue)}</span>
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {learnings.length === 0 && configChanges.length === 0 && (
          <div className="empty-state">
            <div className="icon">🧠</div>
            <div className="title">Waiting for data</div>
            <div className="subtitle">The brain will start learning after a few trades</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Loading Skeleton ────────────────────────────────────────
function LoadingSkeleton() {
  return (
    <div className="panel">
      <div className="panel-header">
        <span>🧠</span> Strategy Brain
        <span className="spinner sm" style={{ marginLeft: 'auto' }} />
      </div>
      <div className="panel-body">
        {/* Health skeleton */}
        <div style={{ textAlign: 'center', marginBottom: 10 }}>
          <div className="skeleton skeleton-text" style={{ width: 60, height: 28, margin: '0 auto' }} />
          <div className="skeleton skeleton-text" style={{ width: 40, height: 10, margin: '4px auto' }} />
          <div className="skeleton" style={{ width: '100%', height: 4, marginTop: 6 }} />
        </div>
        {/* Stats skeleton */}
        <div className="card">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0' }}>
              <div className="skeleton skeleton-text short" style={{ width: '40%' }} />
              <div className="skeleton skeleton-text short" style={{ width: '20%' }} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
