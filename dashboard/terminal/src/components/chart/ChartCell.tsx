// ============================================================
// ChartCell — Self-contained chart panel for multi-monitor layout
// Fixed: stable selectors, timeframe, zoom, crosshair, trade lines
// ============================================================

import { useEffect, useRef, useState } from 'react'
import { createChart, type IChartApi, type ISeriesApi, type CandlestickData, type Time } from 'lightweight-charts'
import { useStore, SYMBOL_CONFIG, ALL_SYMBOLS } from '../../store'
import { SYMBOL_COLORS } from '../../utils/colors'
import { fetchCandles } from '../../services/websocket'
import { sendCommand } from '../../services/websocket'
import type { LayoutCell, Candle } from '../../types'

const TIMEFRAMES = [
  { label: '1M', minutes: 1 },
  { label: '5M', minutes: 5 },
  { label: '15M', minutes: 15 },
  { label: '1H', minutes: 60 },
]

interface ChartCellProps {
  cell: LayoutCell
  isFocused?: boolean
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
  onDragStart?: (cellId: string) => void
  onDragOver?: (cellId: string) => void
  onDrop?: (targetCellId: string) => void
}

export function ChartCell({
  cell,
  isFocused,
  isFullscreen,
  onToggleFullscreen,
  onDragStart,
  onDragOver,
  onDrop,
}: ChartCellProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const [showSelector, setShowSelector] = useState(false)
  const [timeframe, setTimeframe] = useState('1M')
  const [showVolume, setShowVolume] = useState(true)
  const [showTrades, setShowTrades] = useState(true)
  const [alertPrice, setAlertPrice] = useState<number | null>(null)
  const [showAlertInput, setShowAlertInput] = useState(false)
  const [crosshairData, setCrosshairData] = useState<{ time: string; ohlc: string } | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const symbol = cell.symbol

  // Use primitive selectors
  const connected = useStore((s) => s.connected)
  const candleCount = useStore((s) => (s.candles[symbol] || []).length)
  const tradeEventCount = useStore((s) => s.tradeEvents.length)
  const updateLayoutCell = useStore((s) => s.updateLayoutCell)

  // Get data via getState to avoid selector issues
  const candles = useStore.getState().candles[symbol] || []
  const tradeEvents = useStore.getState().tradeEvents

  const cfg = SYMBOL_CONFIG[symbol]
  const color = SYMBOL_COLORS[symbol] || 'var(--accent)'
  const lastCandle = candles[candles.length - 1]
  const isLoading = candles.length === 0 && connected

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
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(59, 130, 246, 0.3)', width: 1, style: 2 },
        horzLine: { color: 'rgba(59, 130, 246, 0.3)', width: 1, style: 2 },
      },
      rightPriceScale: { borderColor: '#1f2937' },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: { vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true },
    })

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    })

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    })
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    })

    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData) {
        setCrosshairData(null)
        return
      }
      const candleData = param.seriesData.get(candleSeries) as CandlestickData<Time> | undefined
      if (candleData) {
        const d = new Date((param.time as number) * 1000)
        const timeStr = d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
        setCrosshairData({
          time: timeStr,
          ohlc: `O ${candleData.open.toFixed(cfg?.digits || 3)} H ${candleData.high.toFixed(cfg?.digits || 3)} L ${candleData.low.toFixed(cfg?.digits || 3)} C ${candleData.close.toFixed(cfg?.digits || 3)}`,
        })
      }
    })

    chartRef.current = chart
    candleSeriesRef.current = candleSeries
    volumeSeriesRef.current = volumeSeries

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

  // Load candles when symbol or timeframe changes
  useEffect(() => {
    if (!symbol) return
    fetchCandles(symbol, 500).then((data) => {
      if (data.length > 0) {
        useStore.getState().setCandles(symbol, data)
      }
    })
  }, [symbol, timeframe])

  // Update candle data when count changes
  useEffect(() => {
    if (!candleSeriesRef.current) return
    const currentCandles = useStore.getState().candles[symbol] || []
    if (currentCandles.length === 0) return

    const data: CandlestickData<Time>[] = currentCandles.map((c) => ({
      time: c.time as Time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    candleSeriesRef.current.setData(data)

    if (volumeSeriesRef.current) {
      const volData = currentCandles.map((c) => ({
        time: c.time as Time,
        value: c.volume || 0,
        color: c.close >= c.open ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)',
      }))
      volumeSeriesRef.current.setData(volData)
    }

    chartRef.current?.timeScale().fitContent()
  }, [symbol, candleCount])

  // Trade markers
  useEffect(() => {
    if (!candleSeriesRef.current || !showTrades) return
    const currentEvents = useStore.getState().tradeEvents

    const markers = currentEvents
      .filter((e) => e.symbol === symbol && (e.type === 'open' || e.type === 'close' || e.type === 'safety_exit'))
      .slice(-30)
      .map((e) => {
        const time = Math.floor(new Date(e.time).getTime() / 1000)
        if (e.type === 'open') {
          return {
            time: time as Time,
            position: e.direction === 1 ? 'belowBar' as const : 'aboveBar' as const,
            color: e.direction === 1 ? '#10b981' : '#ef4444',
            shape: e.direction === 1 ? 'arrowUp' as const : 'arrowDown' as const,
            text: '',
          }
        }
        if (e.type === 'safety_exit') {
          return {
            time: time as Time,
            position: 'aboveBar' as const,
            color: '#f59e0b',
            shape: 'square' as const,
            text: '⚡',
          }
        }
        const isWin = (e.pnl || 0) > 0
        return {
          time: time as Time,
          position: 'aboveBar' as const,
          color: isWin ? '#10b981' : '#ef4444',
          shape: 'circle' as const,
          text: '',
        }
      })

    const seen = new Set<number>()
    const unique = markers.filter((m) => {
      if (seen.has(m.time as number)) return false
      seen.add(m.time as number)
      return true
    })

    candleSeriesRef.current.setMarkers(unique.sort((a, b) => (a.time as number) - (b.time as number)))
  }, [symbol, tradeEventCount, showTrades])

  const handleSymbolChange = (newSymbol: string) => {
    updateLayoutCell(cell.id, newSymbol)
    setShowSelector(false)
  }

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData('text/plain', cell.id)
    setIsDragging(true)
    onDragStart?.(cell.id)
  }

  const handleDragEnd = () => {
    setIsDragging(false)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    onDragOver?.(cell.id)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const sourceId = e.dataTransfer.getData('text/plain')
    if (sourceId !== cell.id) {
      onDrop?.(cell.id)
    }
    setIsDragging(false)
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'var(--bg)',
        border: isFocused ? `1px solid ${color}` : '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        overflow: 'hidden',
        transition: 'all var(--transition-fast)',
        opacity: isDragging ? 0.5 : 1,
      }}
      draggable
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Header */}
      <div style={{
        height: 28,
        background: 'var(--bg2)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 6px',
        gap: 4,
        flexShrink: 0,
        userSelect: 'none',
        cursor: 'grab',
      }}>
        {/* Symbol selector */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowSelector(!showSelector)}
            style={{
              background: 'none',
              border: 'none',
              color: color,
              fontWeight: 700,
              fontSize: 11,
              cursor: 'pointer',
              padding: '2px 6px',
              borderRadius: 'var(--radius-sm)',
              display: 'flex',
              alignItems: 'center',
              gap: 3,
            }}
          >
            {cfg?.short || symbol}
            <span style={{ fontSize: 6, color: 'var(--text-muted)' }}>▼</span>
          </button>

          {showSelector && (
            <div style={{
              position: 'absolute',
              top: '100%',
              left: 0,
              background: 'var(--bg3)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius-sm)',
              zIndex: 100,
              minWidth: 110,
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
              animation: 'fadeInDown 0.15s ease',
            }}>
              {ALL_SYMBOLS.map((sym) => {
                const sCfg = SYMBOL_CONFIG[sym]
                const sColor = SYMBOL_COLORS[sym]
                return (
                  <div
                    key={sym}
                    onClick={() => handleSymbolChange(sym)}
                    style={{
                      padding: '4px 8px',
                      cursor: 'pointer',
                      fontSize: 10,
                      color: sym === symbol ? sColor : 'var(--text-dim)',
                      fontWeight: sym === symbol ? 700 : 400,
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg4)'}
                    onMouseLeave={(e) => e.currentTarget.style.background = 'none'}
                  >
                    <span style={{ width: 5, height: 5, borderRadius: '50%', background: sColor }} />
                    {sCfg?.short || sym}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Timeframe buttons */}
        <div style={{ display: 'flex', gap: 1, marginLeft: 4 }}>
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.label}
              onClick={() => setTimeframe(tf.label)}
              style={{
                padding: '1px 5px',
                border: 'none',
                borderRadius: 2,
                fontSize: 8,
                fontWeight: timeframe === tf.label ? 700 : 400,
                background: timeframe === tf.label ? 'var(--accent)' : 'transparent',
                color: timeframe === tf.label ? '#fff' : 'var(--text-muted)',
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
              }}
            >
              {tf.label}
            </button>
          ))}
        </div>

        {/* Toggle buttons */}
        <div style={{ display: 'flex', gap: 2, marginLeft: 4 }}>
          <ToggleBtn active={showVolume} onClick={() => setShowVolume(!showVolume)} label="V" />
          <ToggleBtn active={showTrades} onClick={() => setShowTrades(!showTrades)} label="T" />
        </div>

        {/* Alert button */}
        <button
          onClick={() => setShowAlertInput(!showAlertInput)}
          style={{
            padding: '1px 5px',
            border: 'none',
            borderRadius: 2,
            fontSize: 8,
            background: alertPrice ? 'var(--yellow-dim)' : 'transparent',
            color: alertPrice ? 'var(--yellow)' : 'var(--text-muted)',
            cursor: 'pointer',
            marginLeft: 2,
          }}
          title="Set price alert"
        >
          🔔
        </button>

        {/* Crosshair data */}
        {crosshairData && (
          <span className="font-mono" style={{ fontSize: 8, color: 'var(--text-muted)', marginLeft: 4 }}>
            {crosshairData.time} {crosshairData.ohlc}
          </span>
        )}

        <div style={{ flex: 1 }} />

        {/* Price */}
        {lastCandle && (
          <span className="font-mono" style={{
            fontSize: 10,
            fontWeight: 600,
            color: lastCandle.close >= lastCandle.open ? 'var(--green)' : 'var(--red)',
          }}>
            {lastCandle.close.toFixed(cfg?.digits || 3)}
          </span>
        )}

        {/* Position */}
        <PositionBadge symbol={symbol} />

        {/* Fullscreen toggle */}
        {onToggleFullscreen && (
          <button
            onClick={onToggleFullscreen}
            style={{
              padding: '1px 5px',
              border: 'none',
              borderRadius: 2,
              fontSize: 9,
              background: 'transparent',
              color: 'var(--text-muted)',
              cursor: 'pointer',
            }}
            title="Toggle fullscreen"
          >
            {isFullscreen ? '⊡' : '⊞'}
          </button>
        )}
      </div>

      {/* Alert input */}
      {showAlertInput && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          padding: '3px 6px',
          background: 'var(--bg3)',
          borderBottom: '1px solid var(--border)',
          fontSize: 9,
        }}>
          <span className="text-muted">Alert @</span>
          <input
            type="number"
            value={alertPrice || ''}
            onChange={(e) => setAlertPrice(parseFloat(e.target.value) || null)}
            placeholder={lastCandle?.close.toFixed(cfg?.digits || 3) || ''}
            style={{
              width: 80,
              padding: '2px 4px',
              background: 'var(--bg)',
              border: '1px solid var(--border)',
              borderRadius: 2,
              color: 'var(--text)',
              fontSize: 9,
              fontFamily: 'var(--mono)',
            }}
          />
          <button
            onClick={() => setShowAlertInput(false)}
            className="btn primary"
            style={{ padding: '1px 6px', fontSize: 8 }}
          >
            Set
          </button>
        </div>
      )}

      {/* Chart */}
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
        </div>
      )}
    </div>
  )
}

// ── Toggle Button ───────────────────────────────────────────
function ToggleBtn({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '1px 5px',
        border: 'none',
        borderRadius: 2,
        fontSize: 8,
        fontWeight: active ? 700 : 400,
        background: active ? 'var(--accent-dim)' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text-muted)',
        cursor: 'pointer',
        transition: 'all var(--transition-fast)',
      }}
    >
      {label}
    </button>
  )
}

// ── Position Badge ──────────────────────────────────────────
function PositionBadge({ symbol }: { symbol: string }) {
  const pos = useStore((s) => s.symbols[symbol]?.position || 'FLAT')
  const pnl = useStore((s) => s.symbols[symbol]?.pnl || 0)

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
      <span className={`badge ${pos.toLowerCase()}`} style={{ fontSize: 7, padding: '1px 4px' }}>
        {pos}
      </span>
      {pnl !== 0 && (
        <span style={{
          fontSize: 8,
          fontFamily: 'var(--mono)',
          fontWeight: 600,
          color: pnl >= 0 ? 'var(--green)' : 'var(--red)',
        }}>
          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}
        </span>
      )}
    </div>
  )
}
