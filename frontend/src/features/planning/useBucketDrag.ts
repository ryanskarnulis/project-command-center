import { type RefObject, useCallback, useEffect, useRef, useState } from 'react'
import type { AxisColumn } from './ganttAxis'
import type { UnscheduledTask } from './ganttModel'

/** Pixels of movement before a press is treated as a drag, not a click. */
const DRAG_THRESHOLD_PX = 4

/** Live state of an in-flight bucket drag, for rendering the ghost + drop target. */
export interface BucketDragState {
  taskId: number
  name: string
  /** Current pointer position, for the floating ghost label. */
  clientX: number
  clientY: number
  /** The column index currently under the pointer, or -1 when off the grid. */
  hoverCol: number
}

interface ActiveBucketDrag {
  taskId: number
  name: string
  startClientX: number
  startClientY: number
  moved: boolean
}

interface UseBucketDrag {
  onItemPointerDown: (task: UnscheduledTask, e: React.PointerEvent) => void
  dragState: BucketDragState | null
  /** True for the click immediately following a real drag; consume to suppress it. */
  justDraggedRef: RefObject<boolean>
}

/**
 * Find which grid column a horizontal pixel position lands in, by hit-testing the
 * rendered `.gantt-col-bg` background cells (which render one-per-column, in
 * order). Returns the column index, or -1 when the pointer is off the columns.
 *
 * Pure given the live DOM — the column geometry is read, never computed here, so
 * no scheduling math lives in the frontend (CLAUDE.md prime directive #1).
 */
export function columnAtClientX(grid: HTMLElement | null, clientX: number): number {
  if (!grid) return -1
  const cells = grid.querySelectorAll('.gantt-col-bg')
  for (let i = 0; i < cells.length; i++) {
    const rect = cells[i].getBoundingClientRect()
    if (clientX >= rect.left && clientX < rect.right) return i
  }
  return -1
}

/**
 * The drag gesture that schedules a task by dropping it from the side
 * "Unscheduled" bucket onto a chart column. Mirrors `useDragReschedule`'s
 * pointer-event lifecycle (the codebase opts out of native HTML5 DnD), but where
 * bar-drag computes a *delta*, a bucket drop is *absolute*: the dropped column's
 * own `iso` becomes the new `scheduled_start`. No API call, no toast — the data
 * owner (`useProjectGantt`) handles persistence, optimism, and feedback.
 */
export function useBucketDrag(
  gridRef: RefObject<HTMLElement | null>,
  columns: AxisColumn[],
  onSchedule: (taskId: number, newStart: string) => void,
): UseBucketDrag {
  const [dragState, setDragState] = useState<BucketDragState | null>(null)
  const active = useRef<ActiveBucketDrag | null>(null)
  const justDraggedRef = useRef(false)

  // Latest callback + columns in refs so the mount-time listeners never go stale.
  const onScheduleRef = useRef(onSchedule)
  useEffect(() => {
    onScheduleRef.current = onSchedule
  }, [onSchedule])
  const columnsRef = useRef(columns)
  useEffect(() => {
    columnsRef.current = columns
  }, [columns])

  useEffect(() => {
    const handleMove = (e: PointerEvent): void => {
      const drag = active.current
      if (!drag) return
      const dx = e.clientX - drag.startClientX
      const dy = e.clientY - drag.startClientY
      if (Math.hypot(dx, dy) > DRAG_THRESHOLD_PX) drag.moved = true
      if (!drag.moved) return
      setDragState({
        taskId: drag.taskId,
        name: drag.name,
        clientX: e.clientX,
        clientY: e.clientY,
        hoverCol: columnAtClientX(gridRef.current, e.clientX),
      })
    }
    const handleUp = (e: PointerEvent): void => {
      const drag = active.current
      if (!drag) return
      justDraggedRef.current = drag.moved
      if (drag.moved) {
        const col = columnAtClientX(gridRef.current, e.clientX)
        const column = columnsRef.current[col]
        if (col >= 0 && column) onScheduleRef.current(drag.taskId, column.iso)
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
  }, [gridRef])

  const onItemPointerDown = useCallback(
    (task: UnscheduledTask, e: React.PointerEvent) => {
      if (e.button !== 0) return // primary button / touch / pen only
      active.current = {
        taskId: task.id,
        name: task.name,
        startClientX: e.clientX,
        startClientY: e.clientY,
        moved: false,
      }
    },
    [],
  )

  return { onItemPointerDown, dragState, justDraggedRef }
}
