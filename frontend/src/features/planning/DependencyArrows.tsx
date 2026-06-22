import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import type { GanttBar } from './ganttModel'
import type { DependencyViolation } from './dependencyConflicts'

// The finish-to-start dependency arrows for the planning Gantt (Slice 4), drawn as
// an SVG overlay *inside* the scrolling `.gantt` grid so the lines scroll with the
// bars. The grid flexes its day columns (`minmax(34px, 1fr)`) and scrolls
// horizontally, so we don't compute geometry from CSS constants — we measure the
// rendered bar rects from the DOM (the same precedent the gesture hooks use for the
// day-column width) and project them into the grid's scroll coordinate space.
//
// The overlay measures against its *own* parent (`.gantt`, the SVG's containing
// element) rather than a passed-in grid ref: a forwarded parent ref isn't reliably
// attached when this child's layout effect first runs, but `svg.parentElement`
// always is.
//
// Non-interactive (`pointer-events: none`): the arrows never block a bar
// drag/click. The per-conflict "Fix" action lives in the conflicts panel, not here.

interface Arrow {
  key: string
  x1: number
  y1: number
  x2: number
  y2: number
  violating: boolean
}

/** Build the `dependent -> [blockerIds]` edges from the drawn bars. */
function edgesFrom(bars: GanttBar[]): { dependentId: number; blockerId: number }[] {
  const edges: { dependentId: number; blockerId: number }[] = []
  for (const bar of bars) {
    for (const blockerId of bar.dependsOn) {
      edges.push({ dependentId: bar.id, blockerId })
    }
  }
  return edges
}

export function DependencyArrows({
  bars,
  violations,
}: {
  bars: GanttBar[]
  violations: DependencyViolation[]
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [arrows, setArrows] = useState<Arrow[]>([])
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 })

  const measure = useCallback(() => {
    const grid = svgRef.current?.parentElement
    if (!grid) return
    const gridRect = grid.getBoundingClientRect()
    // Each finish->start violation is keyed by its (dependent, blocker) pair so the
    // arrow for exactly that edge can be colored independently.
    const violatingPairs = new Set(
      violations.map((v) => `${v.dependentId}->${v.blockerId}`),
    )

    /** A bar's rect in the grid's scroll coordinate space, or null if not drawn. */
    const rectOf = (
      id: number,
    ): { left: number; right: number; mid: number } | null => {
      const el = grid.querySelector<HTMLElement>(`[data-bar-id="${id}"]`)
      if (!el) return null
      const r = el.getBoundingClientRect()
      return {
        left: r.left - gridRect.left + grid.scrollLeft,
        right: r.right - gridRect.left + grid.scrollLeft,
        mid: r.top - gridRect.top + grid.scrollTop + r.height / 2,
      }
    }

    const next: Arrow[] = []
    for (const { dependentId, blockerId } of edgesFrom(bars)) {
      const blocker = rectOf(blockerId)
      const dependent = rectOf(dependentId)
      if (!blocker || !dependent) continue
      next.push({
        key: `${dependentId}->${blockerId}`,
        x1: blocker.right,
        y1: blocker.mid,
        x2: dependent.left,
        y2: dependent.mid,
        violating: violatingPairs.has(`${dependentId}->${blockerId}`),
      })
    }
    setArrows(next)
    setSize({ w: grid.scrollWidth, h: grid.scrollHeight })
  }, [bars, violations])

  // Re-measure on first paint, on any data change, and whenever the grid resizes
  // (column flex, window resize, content reflow). Scroll needs no listener: the SVG
  // is a child of the scroll container, so it translates with the content.
  useLayoutEffect(() => {
    measure()
    const grid = svgRef.current?.parentElement
    if (!grid || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => measure())
    ro.observe(grid)
    return () => ro.disconnect()
  }, [measure])

  return (
    <svg
      ref={svgRef}
      className="gantt-deps"
      width={size.w}
      height={size.h}
      aria-hidden="true"
    >
      <defs>
        <marker
          id="gantt-dep-arrow"
          markerWidth="6"
          markerHeight="6"
          refX="5"
          refY="3"
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <path d="M0,0 L6,3 L0,6 Z" className="gantt-dep-head" />
        </marker>
        <marker
          id="gantt-dep-arrow-bad"
          markerWidth="6"
          markerHeight="6"
          refX="5"
          refY="3"
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <path d="M0,0 L6,3 L0,6 Z" className="gantt-dep-head is-violating" />
        </marker>
      </defs>
      {arrows.map((a) => {
        // An orthogonal elbow reads more clearly than a diagonal when bars sit on
        // different rows: out from the blocker, across, then into the dependent.
        const midX = a.x2 >= a.x1 ? (a.x1 + a.x2) / 2 : a.x1 + 12
        const d = `M ${a.x1} ${a.y1} L ${midX} ${a.y1} L ${midX} ${a.y2} L ${a.x2} ${a.y2}`
        return (
          <path
            key={a.key}
            d={d}
            className={`gantt-dep-line${a.violating ? ' is-violating' : ''}`}
            markerEnd={`url(#${a.violating ? 'gantt-dep-arrow-bad' : 'gantt-dep-arrow'})`}
          />
        )
      })}
    </svg>
  )
}
