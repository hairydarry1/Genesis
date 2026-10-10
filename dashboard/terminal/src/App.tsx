// ============================================================
// Spike Catcher — Main App
// ============================================================

import { useEffect, useState } from 'react'
import { useStore } from './store'
import { connectWebSocket, disconnectWebSocket } from './services/websocket'
import { TitleBar } from './components/layout/TitleBar'
import { SymbolPanel } from './components/panels/SymbolPanel'
import { CandleChart } from './components/chart/CandleChart'
import { BrainPanel } from './components/panels/BrainPanel'
import { EquityChart } from './components/chart/EquityChart'
import { BottomTabs } from './components/layout/BottomTabs'
import { ControlPanel } from './components/panels/ControlPanel'
import { MultiChartGrid } from './components/chart/MultiChartGrid'

export default function App() {
  const bottomTab = useStore((s) => s.bottomTab)
  const connected = useStore((s) => s.connected)
  const layoutMode = useStore((s) => s.layoutMode)
  const [showSplash, setShowSplash] = useState(true)

  useEffect(() => {
    connectWebSocket()
    const timer = setTimeout(() => setShowSplash(false), 1500)
    return () => {
      disconnectWebSocket()
      clearTimeout(timer)
    }
  }, [])

  // Splash screen
  if (showSplash) {
    return (
      <div style={{
        height: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg)',
        animation: 'fadeIn 0.3s ease',
      }}>
        <div style={{
          fontSize: 32,
          fontWeight: 800,
          color: 'var(--accent)',
          letterSpacing: 2,
          marginBottom: 8,
        }}>
          ⚡ GENESIS
        </div>
        <div style={{
          fontSize: 12,
          color: 'var(--text-dim)',
          letterSpacing: 1,
          marginBottom: 16,
        }}>
          SPIKE CATCHER
        </div>
        <div className="spinner" />
        <div style={{
          fontSize: 10,
          color: 'var(--text-muted)',
          marginTop: 12,
        }}>
          Connecting to brain bridge...
        </div>
      </div>
    )
  }

  const isMultiLayout = layoutMode !== 'single'

  return (
    <div className="app-layout">
      {/* Title Bar */}
      <TitleBar />

      {/* Main Grid */}
      <div className="main-grid">
        {/* Left: Symbol Panel (always visible) */}
        <SymbolPanel />

        {/* Center: Chart Area */}
        <div className="panel" style={{
          display: 'flex',
          flexDirection: 'column',
          position: 'relative',
          overflow: 'hidden',
        }}>
          {isMultiLayout ? (
            <MultiChartGrid />
          ) : (
            <CandleChart />
          )}
        </div>

        {/* Right: Brain / Control Panel */}
        {bottomTab === 'control' ? <ControlPanel /> : <BrainPanel />}
      </div>

      {/* Bottom: Equity Curve + Tabs */}
      <div className="bottom-panel">
        <EquityChart />
        <BottomTabs />
      </div>
    </div>
  )
}
