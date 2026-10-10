// ============================================================
// CandleChart — Live candlestick chart with trade markers
// Fixed: stable selectors to prevent infinite re-render
// ============================================================

import { useEffect, useRef, useMemo } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type CandlestickData, type Time } from 'lightweight-charts'
import { useStore, SYMBOL_CONFIG } from '../../store'
import { SYMBOL_COLORS } from '../../utils/colors'
import { fetchCandles } from '../../services/websocket'

export function CandleChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  // Use primitive selectors to avoid infinite re-render
  const activeSymbol = useStore((s) => s.activeSymbol)
  const connected = useStore((s) => s.connected)
  const candleCount = useStore((s) => (s.candles[s.activeSymbol] || []).length)
  const tradeEventCount = useStore((s) => s.tradeEvents.length)

  // Get data via refs to avoid selector re-renders
  const candles = useStore.getState().candles[activeSymbol] || []
  const tradeEvents = useStore.getState().tradeEvents

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#0a0e17' },
        textColor: '#9ca3af',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: '#111827' },
        horzLines: { color: '#111827' },
      },
      crosshair: { mode: 0 },
      rightPriceScale: { borderColor: '#1f2937' },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
      },
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        if (width > 0 && height > 0) {
          chart.applyOptions({ width, height })
        }
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
    }
  }, [])

  // Load candles when symbol changes
  useEffect(() => {
    if (!activeSymbol) return
    fetchCandles(activeSymbol, 500).then((data) => {
      if (data.length > 0) {
        useStore.getState().setCandles(activeSymbol, data)
      }
    })
  }, [activeSymbol])

  // Update candle data when count changes
  useEffect(() => {
    if (!candleSeriesRef.current) return
    const currentCandles = useStore.getState().candles[activeSymbol] || []
    if (currentCandles.length === 0) return

    const data: CandlestickData<Time>[] = currentCandles.map((c) => ({
      time: c.time as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    candleSeriesRef.current.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [activeSymbol, candleCount])

  // Add trade markers when events change
  useEffect(() => {
    if (!candleSeriesRef.current) return
    const currentEvents = useStore.getState().tradeEvents

    const markers = currentEvents
      .filter((e) => e.symbol === activeSymbol && (e.type === 'open' || e.type === 'close' || e.type === 'safety_exit'))
      .slice(-50)
      .map((e) => {
        const time = Math.floor(new Date(e.time).getTime() / 1000)
        if (e.type === 'open') {
          return {
            time: time as Time,
            position: e.direction === 1 ? 'belowBar' as const : 'aboveBar' as const,
            color: e.direction === 1 ? '#10b981' : '#ef4444',
            shape: e.direction === 1 ? 'arrowUp' as const : 'arrowDown' as const,
            text: `${e.side} @ ${(e.entryPrice || 0).toFixed(2)}`,
          }
        }
        if (e.type === 'safety_exit') {
          return {
            time: time as Time,
            position: 'aboveBar' as const,
            color: '#f59e0b',
            shape: 'square' as const,
            text: `⚡ ${(e.pnl || 0).toFixed(2)}`,
          }
        }
        const isWin = (e.pnl || 0) > 0
        return {
          time: time as Time,
          position: 'aboveBar' as const,
          color: isWin ? '#10b981' : '#ef4444',
          shape: 'circle' as const,
          text: `${isWin ? '✓' : '✗'} ${(e.pnl || 0).toFixed(2)}`,
        }
      })

    const seen = new Set<number>()
    const unique = markers.filter((m) => {
      if (seen.has(m.time as number)) return false
      seen.add(m.time as number)
      return true
    })

    candleSeriesRef.current.setMarkers(unique.sort((a, b) => (a.time as number) - (b.time as number)))
  }, [activeSymbol, tradeEventCount])

  // Get display data
  const displayCandles = useStore.getState().candles[activeSymbol] || []
  const lastCandle = displayCandles[displayCandles.length - 1]
  const symColor = SYMBOL_COLORS[activeSymbol] || 'var(--accent)'
  const isLoading = displayCandles.length === 0 && connected

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Chart Header */}
      <div style={{
        height: 28,
        background: 'var(--bg2)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 10px',
        gap: 8,
        flexShrink: 0,
      }}>
        <span className="font-bold" style={{ fontSize: 11, color: symColor }}>
          {SYMBOL_CONFIG[activeSymbol]?.short || activeSymbol}
        </span>
        <span className="font-mono text-dim" style={{ fontSize: 10 }}>
          M1
        </span>
        {isLoading && <span className="spinner sm" />}
        {lastCandle && (
          <span className="font-mono text-dim" style={{ fontSize: 10 }}>
            O {lastCandle.open.toFixed(3)} H {lastCandle.high.toFixed(3)} L {lastCandle.low.toFixed(3)} C {lastCandle.close.toFixed(3)}
          </span>
        )}
      </div>

      {/* Chart Container */}
      <div ref={containerRef} style={{ flex: 1, minHeight: 0 }} />

      {/* Loading overlay */}
      {isLoading && (
        <div className="empty-state" style={{
          position: 'absolute',
          inset: 0,
          top: 28,
          background: 'rgba(10, 14, 23, 0.8)',
        }}>
          <span className="spinner lg" />
          <div className="title" style={{ marginTop: 8 }}>Loading candles...</div>
        </div>
      )}
    </div>
  )
}
