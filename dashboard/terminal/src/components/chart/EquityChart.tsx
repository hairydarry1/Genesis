// ============================================================
// EquityChart — Per-symbol equity curves
// ============================================================

import { useEffect, useRef } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import { useStore, ALL_SYMBOLS } from '../../store'
import { SYMBOL_COLORS } from '../../utils/colors'

export function EquityChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRefs = useRef<Record<string, ISeriesApi<'Line'>>>({})
  const equityCurves = useStore((s) => s.equityCurves)
  const connected = useStore((s) => s.connected)

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#0a0e17' },
        textColor: '#6b7280',
        fontSize: 9,
      },
      grid: {
        vertLines: { color: '#111827' },
        horzLines: { color: '#1f2937' },
      },
      rightPriceScale: { borderColor: '#1f2937' },
      timeScale: { visible: false },
      handleScroll: false,
      handleScale: false,
    })

    chartRef.current = chart

    // Create a line series for each symbol
    for (const sym of ALL_SYMBOLS) {
      const color = SYMBOL_COLORS[sym] || '#6b7280'
      const series = chart.addLineSeries({
        color,
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      seriesRefs.current[sym] = series
    }

    // Resize observer
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        chart.applyOptions({ width, height })
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [])

  // Update data
  useEffect(() => {
    if (!chartRef.current) return

    let hasData = false
    for (const sym of ALL_SYMBOLS) {
      const series = seriesRefs.current[sym]
      if (!series) continue

      const points = equityCurves[sym] || []
      if (points.length === 0) continue

      hasData = true
      const data = points.map((p, i) => ({
        time: (Math.floor(new Date(p.time).getTime() / 1000) + i) as Time,
        value: p.equity,
      }))
      series.setData(data)
    }

    if (hasData) {
      chartRef.current.timeScale().fitContent()
    }
  }, [equityCurves])

  // Calculate combined stats
  const totalTrades = ALL_SYMBOLS.reduce((sum, s) => sum + (equityCurves[s]?.length || 0), 0)

  return (
    <div style={{ position: 'relative', height: 120, borderBottom: '1px solid var(--border)' }}>
      {/* Label */}
      <div style={{
        position: 'absolute',
        top: 4,
        left: 8,
        fontSize: 9,
        color: 'var(--text-muted)',
        textTransform: 'uppercase',
        letterSpacing: 0.5,
        zIndex: 1,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}>
        📈 Equity Curve
        {!connected && <span className="spinner sm" />}
      </div>

      {/* Legend */}
      <div style={{
        position: 'absolute',
        top: 4,
        right: 8,
        display: 'flex',
        gap: 10,
        fontSize: 9,
        zIndex: 1,
      }}>
        {ALL_SYMBOLS.map((sym) => {
          const points = equityCurves[sym] || []
          const lastEq = points.length > 0 ? points[points.length - 1].equity : 10000
          const ret = ((lastEq / 10000 - 1) * 100).toFixed(2)
          return (
            <span key={sym} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 8, height: 2, borderRadius: 1, background: SYMBOL_COLORS[sym] }} />
              <span style={{ color: SYMBOL_COLORS[sym] }}>{sym.replace('500', '').replace('1000', '')}</span>
              <span style={{ color: lastEq >= 10000 ? 'var(--green)' : 'var(--red)' }}>
                ${lastEq.toFixed(0)} ({ret}%)
              </span>
            </span>
          )
        })}
      </div>

      {/* Chart */}
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />

      {/* Empty state */}
      {totalTrades === 0 && (
        <div className="empty-state" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
          <div style={{ fontSize: 20, marginBottom: 4, opacity: 0.4 }}>📈</div>
          <div className="title" style={{ fontSize: 10 }}>No trades yet</div>
          <div className="subtitle" style={{ fontSize: 9 }}>Equity curve will appear as trades close</div>
        </div>
      )}
    </div>
  )
}
