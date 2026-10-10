// ============================================================
// ControlPanel — Start/stop, risk management, force close
// ============================================================

import { useStore, SYMBOL_CONFIG, ALL_SYMBOLS } from '../../store'
import { sendCommand } from '../../services/websocket'

export function ControlPanel() {
  const symbols = useStore((s) => s.symbols)
  const tradingActive = useStore((s) => s.tradingActive)
  const setTradingActive = useStore((s) => s.setTradingActive)
  const connected = useStore((s) => s.connected)
  const riskOverrides = useStore((s) => s.riskOverrides)

  const handleStart = () => {
    sendCommand({ action: 'start', symbols: ALL_SYMBOLS })
    setTradingActive(true)
  }

  const handleStop = () => {
    sendCommand({ action: 'stop' })
    setTradingActive(false)
  }

  const handleSetRisk = (symbol: string, risk: number) => {
    sendCommand({ action: 'set_risk', symbol, risk })
    useStore.getState().setRiskOverride(symbol, risk)
  }

  const handleForceClose = (symbol: string) => {
    sendCommand({ action: 'force_close', symbol })
  }

  return (
    <div className="panel" style={{ overflowY: 'auto' }}>
      <div className="panel-header">
        <span>⚙️</span> Control Panel
      </div>
      <div className="panel-body">
        {/* Backend Status */}
        <div style={{ marginBottom: 10 }}>
          <div style={{
            fontSize: 9,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            color: 'var(--text-muted)',
            marginBottom: 6,
          }}>
            Backend Status
          </div>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 8px',
            background: 'var(--bg3)',
            borderRadius: 'var(--radius-md)',
          }}>
            <div className={`dot ${connected ? 'connected' : 'disconnected'}`} />
            <span style={{
              fontSize: 10,
              fontWeight: 600,
              color: connected ? 'var(--green)' : 'var(--red)',
            }}>
              {connected ? 'Connected' : 'Disconnected'}
            </span>
            {connected && (
              <span style={{ marginLeft: 'auto', fontSize: 8, color: 'var(--text-muted)' }}>
                ● Active
              </span>
            )}
          </div>
        </div>

        {/* Trading Controls */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
          <button
            className="btn success"
            onClick={handleStart}
            disabled={!connected || tradingActive}
            style={{ flex: 1 }}
          >
            ▶ Start
          </button>
          <button
            className="btn warning"
            onClick={handleStop}
            disabled={!tradingActive}
            style={{ flex: 1 }}
          >
            ⏸ Pause
          </button>
        </div>

        {/* Per-Symbol Risk */}
        <div style={{
          fontSize: 9,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
          color: 'var(--text-muted)',
          marginBottom: 8,
        }}>
          Per-Symbol Risk
        </div>

        {ALL_SYMBOLS.map((sym, i) => {
          const state = symbols[sym]
          const cfg = SYMBOL_CONFIG[sym]
          const risk = riskOverrides[sym] ?? state?.riskPct ?? 0.10

          return (
            <div
              key={sym}
              className="card"
              style={{
                marginBottom: 6,
                animation: `fadeInUp ${0.1 + i * 0.05}s ease`,
                borderLeftColor: state?.position !== 'FLAT' ? 'var(--green)' : 'var(--accent)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span className="font-bold" style={{ fontSize: 11, color: 'var(--text)' }}>
                  {cfg.short}
                </span>
                <span className="font-mono" style={{
                  fontSize: 11,
                  color: 'var(--accent)',
                  fontWeight: 700,
                }}>
                  {(risk * 100).toFixed(0)}%
                </span>
              </div>

              <input
                type="range"
                min="1"
                max="30"
                value={risk * 100}
                onChange={(e) => handleSetRisk(sym, parseInt(e.target.value) / 100)}
              />

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 6 }}>
                <button
                  className="btn danger"
                  onClick={() => handleForceClose(sym)}
                  disabled={state?.position === 'FLAT'}
                  style={{ fontSize: 8, padding: '3px 8px' }}
                >
                  Force Close
                </button>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <span className={`badge ${(state?.position || 'FLAT').toLowerCase()}`}>
                    {state?.position || 'FLAT'}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
