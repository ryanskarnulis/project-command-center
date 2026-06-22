import { type RefObject, useCallback, useEffect, useRef, useState } from 'react'
import { addDays } from './ganttModel'
import type { GanttBar } from './ganttModel'

/** Pixels of horizontal movement before a press is treated as a drag, not a click. */
const DRAG_THRESHOLD_PX = 4

/** Live state of an in-flight bar drag, for rendering the preview. */
export interface DragState {
  barId: number
  /** Whole days the bar has moved from its start (snaps to day boundaries). */
  deltaDays: number
  /** Measured width of one day column, so the preview can translate in pixels. */
  dayWidth: number
}

interface ActiveDrag {
  barId: number
  start: string
  startClientX: number
  dayWidth: number
  moved: boolean
}

interface UseDragReschedule {
  onBarPointerDown: (bar: GanttBar, e: React.PointerEvent) => void
  dragState: DragState | null
  /** True for the click immediately following a real drag; consume to suppress it. */
  justDraggedRef: RefObject<boolean>
}

/**
 * The horizontal drag gesture for Gantt bars — and only the gesture. It measures
 * the day-column width from the live grid (columns flex, so the per-day pixel
 * width is not a constant), converts the pointer delta to whole days, and on
 * release hands a new start date to `onReschedule`. No API call, no toast: the
 * data owner (`useProjectGantt`) handles persistence, optimism, and feedback.
 *
 * The window move/up listeners are attached once and early-return while idle —
 * simpler than add/remove-per-drag and avoids a handler dependency cycle.
 */
export function useDragReschedule(
  gridRef: RefObject<HTMLElement | null>,
  onReschedule: (taskId: number, newStart: string) => void,
): UseDragReschedule {
  const [dragState, setDragState] = useState<DragState | null>(null)
  const active = useRef<ActiveDrag | null>(null)
  const justDraggedRef = useRef(false)

  // Latest callback in a ref so the mount-time listeners never go stale.
  const onRescheduleRef = useRef(onReschedule)
  useEffect(() => {
    onRescheduleRef.current = onReschedule
  }, [onReschedule])

  useEffect(() => {
    const handleMove = (e: PointerEvent): void => {
      const drag = active.current
      if (!drag) return
      const dx = e.clientX - drag.startClientX
      if (Math.abs(dx) > DRAG_THRESHOLD_PX) drag.moved = true
      const deltaDays = Math.round(dx / drag.dayWidth)
      setDragState({ barId: drag.barId, deltaDays, dayWidth: drag.dayWidth })
    }
    const handleUp = (e: PointerEvent): void => {
      const drag = active.current
      if (!drag) return
      const deltaDays = Math.round((e.clientX - drag.startClientX) / drag.dayWidth)
      justDraggedRef.current = drag.moved
      if (drag.moved && deltaDays !== 0) {
        onRescheduleRef.current(drag.barId, addDays(drag.start, deltaDays))
      }
      active.current = null
      setDragState(null)
    }
    const handleCancel = (): void => {
      active.current = null
      setDragState(null)
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

  const onBarPointerDown = useCallback(
    (bar: GanttBar, e: React.PointerEvent) => {
      if (e.button !== 0) return // primary button / touch / pen only
      const cell = gridRef.current?.querySelector('.gantt-col-bg')
      const dayWidth = cell?.getBoundingClientRect().width ?? 0
      if (dayWidth <= 0) return // can't map pixels → days; leave it a plain click
      e.preventDefault()
      active.current = {
        barId: bar.id,
        start: bar.start,
        startClientX: e.clientX,
        dayWidth,
        moved: false,
      }
      setDragState({ barId: bar.id, deltaDays: 0, dayWidth })
    },
    [gridRef],
  )

  return { onBarPointerDown, dragState, justDraggedRef }
}
