import { type RefObject, useCallback, useEffect, useRef, useState } from 'react'
import { spanDays } from './ganttModel'
import type { GanttBar } from './ganttModel'

/** Pixels of horizontal movement before a press is treated as a resize, not a click. */
const DRAG_THRESHOLD_PX = 4

/** Minutes one whole day-column represents (8h * 60), mirrors ganttModel. */
const MINUTES_PER_DAY = 480

/** Live state of an in-flight bar resize, for rendering the preview. */
export interface ResizeState {
  barId: number
  /** Previewed span in whole day columns (always at least 1). */
  newSpan: number
  /** Measured width of one day column, so the preview can size in pixels. */
  dayWidth: number
}

interface ActiveResize {
  barId: number
  startSpan: number
  startClientX: number
  dayWidth: number
  newSpan: number
  moved: boolean
}

interface UseBarResize {
  onHandlePointerDown: (bar: GanttBar, e: React.PointerEvent) => void
  resizeState: ResizeState | null
  /** True for the click immediately following a real resize; consume to suppress it. */
  justResizedRef: RefObject<boolean>
}

/**
 * The right-edge resize gesture for Gantt bars — and only the gesture. It mirrors
 * `useDragReschedule`: it measures the flexing day-column width from the live grid,
 * converts the pointer delta to a whole-day span change (clamped to a 1-day floor),
 * and on release hands the new estimate (span × 480 min) to `onResize`. No API call,
 * no toast, no parent-override prompt: the data owner (`useProjectGantt`) handles
 * persistence, optimism, feedback, and the prompt.
 *
 * The window move/up listeners are attached once and early-return while idle.
 */
export function useBarResize(
  gridRef: RefObject<HTMLElement | null>,
  onResize: (taskId: number, newMinutes: number) => void,
): UseBarResize {
  const [resizeState, setResizeState] = useState<ResizeState | null>(null)
  const active = useRef<ActiveResize | null>(null)
  const justResizedRef = useRef(false)

  // Latest callback in a ref so the mount-time listeners never go stale.
  const onResizeRef = useRef(onResize)
  useEffect(() => {
    onResizeRef.current = onResize
  }, [onResize])

  useEffect(() => {
    const handleMove = (e: PointerEvent): void => {
      const drag = active.current
      if (!drag) return
      const dx = e.clientX - drag.startClientX
      if (Math.abs(dx) > DRAG_THRESHOLD_PX) drag.moved = true
      const deltaDays = Math.round(dx / drag.dayWidth)
      const newSpan = Math.max(1, drag.startSpan + deltaDays)
      drag.newSpan = newSpan
      setResizeState({ barId: drag.barId, newSpan, dayWidth: drag.dayWidth })
    }
    const handleUp = (): void => {
      const drag = active.current
      if (!drag) return
      justResizedRef.current = drag.moved
      if (drag.moved && drag.newSpan !== drag.startSpan) {
        onResizeRef.current(drag.barId, drag.newSpan * MINUTES_PER_DAY)
      }
      active.current = null
      setResizeState(null)
    }
    const handleCancel = (): void => {
      active.current = null
      setResizeState(null)
    }
    window.addEventListener('pointermove', handleMove)
    window.addEventListener('pointerup', handleUp)
    window.addEventListener('pointercancel', handleCancel)
    return () => {
      window.removeEventListener('pointermove', handleMove)
      window.removeEventListener('pointerup', handleUp)
      window.removeEventListener('pointercancel', handleCancel)
    }
  }, [])

  const onHandlePointerDown = useCallback(
    (bar: GanttBar, e: React.PointerEvent) => {
      if (e.button !== 0) return // primary button / touch / pen only
      const cell = gridRef.current?.querySelector('.gantt-col-bg')
      const dayWidth = cell?.getBoundingClientRect().width ?? 0
      if (dayWidth <= 0) return // can't map pixels → days; leave it a plain click
      e.preventDefault()
      const startSpan = spanDays(bar.estimatedMinutes)
      active.current = {
        barId: bar.id,
        startSpan,
        startClientX: e.clientX,
        dayWidth,
        newSpan: startSpan,
        moved: false,
      }
      setResizeState({ barId: bar.id, newSpan: startSpan, dayWidth })
    },
    [gridRef],
  )

  return { onHandlePointerDown, resizeState, justResizedRef }
}
