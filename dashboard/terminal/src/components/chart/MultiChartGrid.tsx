// ============================================================
// MultiChartGrid — Arranges ChartCells with drag-to-swap, resize, fullscreen
// ============================================================

import { useState, useCallback, useRef, useEffect } from 'react'
import { useStore } from '../../store'
import { ChartCell } from './ChartCell'
import type { LayoutMode, LayoutCell } from '../../types'

const GRID_STYLES: Record<LayoutMode, React.CSSProperties> = {
  single: {
    display: 'grid',
    gridTemplateColumns: '1fr',
    gridTemplateRows: '1fr',
    gap: 1,
    height: '100%',
  },
  dual: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gridTemplateRows: '1fr',
    gap: 1,
    height: '100%',
  },
  quad: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gridTemplateRows: '1fr 1fr',
    gap: 1,
    height: '100%',
  },
  'triple-left': {
    display: 'grid',
    gridTemplateColumns: '1.5fr 1fr',
    gridTemplateRows: '1fr 1fr',
    gap: 1,
    height: '100%',
  },
  'triple-right': {
    display: 'grid',
    gridTemplateColumns: '1fr 1.5fr',
    gridTemplateRows: '1fr 1fr',
    gap: 1,
    height: '100%',
  },
}

const GRID_AREAS: Record<LayoutMode, string[]> = {
  single: ['1 / 1 / 2 / 2'],
  dual: ['1 / 1 / 2 / 2', '1 / 2 / 2 / 3'],
  quad: ['1 / 1 / 2 / 2', '1 / 2 / 2 / 3', '2 / 1 / 3 / 2', '2 / 2 / 3 / 3'],
  'triple-left': ['1 / 1 / 3 / 2', '1 / 2 / 2 / 3', '2 / 2 / 3 / 3'],
  'triple-right': ['1 / 1 / 2 / 2', '2 / 1 / 3 / 2', '1 / 2 / 3 / 3'],
}

export function MultiChartGrid() {
  const layoutMode = useStore((s) => s.layoutMode)
  const layoutCells = useStore((s) => s.layoutCells)
  const activeSymbol = useStore((s) => s.activeSymbol)
  const setLayoutCells = useStore((s) => s.setLayoutCells)

  const [fullscreenCell, setFullscreenCell] = useState<string | null>(null)
  const [dragSource, setDragSource] = useState<string | null>(null)
  const [dragTarget, setDragTarget] = useState<string | null>(null)
  const [columnWidths, setColumnWidths] = useState<number[]>([])
  const [rowHeights, setRowHeights] = useState<number[]>([])
  const gridRef = useRef<HTMLDivElement>(null)
  const isResizing = useRef(false)

  const gridStyle = GRID_STYLES[layoutMode]
  const areas = GRID_AREAS[layoutMode]

  // Drag-to-swap handlers
  const handleDragStart = useCallback((cellId: string) => {
    setDragSource(cellId)
  }, [])

  const handleDragOver = useCallback((cellId: string) => {
    setDragTarget(cellId)
  }, [])

  const handleDrop = useCallback((targetCellId: string) => {
    if (dragSource && dragSource !== targetCellId) {
      // Swap symbols between cells
      const newCells = layoutCells.map((c) => {
        if (c.id === dragSource) {
          const target = layoutCells.find((tc) => tc.id === targetCellId)
          return { ...c, symbol: target?.symbol || c.symbol }
        }
        if (c.id === targetCellId) {
          const source = layoutCells.find((sc) => sc.id === dragSource)
          return { ...c, symbol: source?.symbol || c.symbol }
        }
        return c
      })
      setLayoutCells(newCells)
    }
    setDragSource(null)
    setDragTarget(null)
  }, [dragSource, layoutCells, setLayoutCells])

  // Fullscreen toggle
  const handleToggleFullscreen = useCallback((cellId: string) => {
    setFullscreenCell((prev) => (prev === cellId ? null : cellId))
  }, [])

  // Resizable column/row handlers
  const handleColumnResize = useCallback((index: number, e: React.MouseEvent) => {
    e.preventDefault()
    isResizing.current = true
    const startX = e.clientX
    const grid = gridRef.current
    if (!grid) return

    const startWidths = [...columnWidths]
    if (startWidths.length === 0) {
      const rect = grid.getBoundingClientRect()
      const cols = layoutMode.includes('dual') || layoutMode === 'quad' ? 2 : layoutMode.includes('triple') ? 2 : 1
      const colWidth = rect.width / cols
      for (let i = 0; i < cols; i++) startWidths.push(colWidth)
    }

    const onMouseMove = (moveE: MouseEvent) => {
      const delta = moveE.clientX - startX
      const newWidths = [...startWidths]
      newWidths[index] = Math.max(100, startWidths[index] + delta)
      newWidths[index + 1] = Math.max(100, startWidths[index + 1] - delta)
      setColumnWidths(newWidths)
    }

    const onMouseUp = () => {
      isResizing.current = false
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }, [columnWidths, layoutMode])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && fullscreenCell) {
        setFullscreenCell(null)
      }
      // Number keys 1-4 to switch layout
      if (e.key === '1' && !e.ctrlKey && !e.metaKey) {
        useStore.getState().setLayoutMode('single')
      }
      if (e.key === '2' && !e.ctrlKey && !e.metaKey) {
        useStore.getState().setLayoutMode('dual')
      }
      if (e.key === '3' && !e.ctrlKey && !e.metaKey) {
        useStore.getState().setLayoutMode('quad')
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [fullscreenCell])

  // Fullscreen mode
  if (fullscreenCell) {
    const cell = layoutCells.find((c) => c.id === fullscreenCell)
    if (cell) {
      return (
        <div style={{ height: '100%', position: 'relative' }}>
          <ChartCell
            cell={cell}
            isFocused={cell.symbol === activeSymbol}
            isFullscreen={true}
            onToggleFullscreen={() => handleToggleFullscreen(cell.id)}
          />
        </div>
      )
    }
  }

  return (
    <div ref={gridRef} style={{ ...gridStyle, position: 'relative', height: '100%' }}>
      {layoutCells.map((cell, i) => (
        <div
          key={cell.id}
          style={{
            gridArea: areas[i] || cell.gridArea,
            minHeight: 0,
            position: 'relative',
            outline: dragTarget === cell.id ? '2px solid var(--accent)' : 'none',
            outlineOffset: -1,
            transition: 'outline var(--transition-fast)',
          }}
        >
          <ChartCell
            cell={cell}
            isFocused={cell.symbol === activeSymbol}
            isFullscreen={false}
            onToggleFullscreen={() => handleToggleFullscreen(cell.id)}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
          />
        </div>
      ))}
    </div>
  )
}
