import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { getProjectGantt } from '../../api/planning'
import { updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { ProjectGantt } from '../../types/planning'

interface UseProjectGantt {
  data: ProjectGantt | null
  loading: boolean
  error: string | null
  /** Optimistically move a task's bar, persist via PATCH, revert on error. */
  reschedule: (taskId: number, newStart: string) => Promise<void>
  /** Optimistically clear a task's scheduled start (take it off the timeline). */
  unschedule: (taskId: number) => Promise<void>
  /** Optimistically resize a task's estimate, persist via PATCH, revert on error. */
  resize: (taskId: number, newMinutes: number) => Promise<void>
  /** Re-fetch the planning payload (e.g. after a what-if commit persists changes). */
  refetch: () => void
}

/** Best-effort message from an unknown error, preferring the API `detail`. */
function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    return (err.body as { detail?: string })?.detail ?? `Error ${err.status}`
  }
  if (err instanceof Error) return err.message
  return fallback
}

/**
 * Fetch the planning payload for a project, with the shared loading/error
 * baseline, and own the drag-to-reschedule mutation: the data lives here, so the
 * optimistic move (and its revert on failure) belongs here too. Refetches when
 * the project id changes.
 */
export function useProjectGantt(projectId: number): UseProjectGantt {
  const [data, setData] = useState<ProjectGantt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedId, setLoadedId] = useState<number | null>(null)
  const { notify } = useToast()

  const load = useCallback(
    (active: () => boolean = () => true) => {
      getProjectGantt(projectId)
        .then((result) => {
          if (!active()) return
          setData(result)
          setError(null)
          setLoadedId(projectId)
        })
        .catch((err: unknown) => {
          if (!active()) return
          setError(errorMessage(err, 'Failed to load the timeline'))
          setLoadedId(projectId)
        })
    },
    [projectId],
  )

  useEffect(() => {
    let active = true
    load(() => active)
    return () => {
      active = false
    }
  }, [load])

  const reschedule = useCallback(
    async (taskId: number, newStart: string) => {
      const snapshot = data
      // Optimistic: move the bar now so the drag feels instant. The
      // `buildGanttModel` memo in TimelinePage re-places it from this state.
      setData((prev) =>
        prev === null
          ? prev
          : {
              ...prev,
              tasks: prev.tasks.map((t) =>
                t.id === taskId ? { ...t, scheduled_start: newStart } : t,
              ),
            },
      )
      try {
        await updateTask(taskId, { scheduled_start: newStart })
        notify('success', 'Task rescheduled')
        // Reconcile from the server: derived flags (conflict outline,
        // blocked/blocking) *and* any downstream dependents the PATCH auto-shifted
        // (Slice 5 — the cascade runs server-side, so the refetch surfaces it).
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to reschedule task'))
      }
    },
    [data, load, notify],
  )

  const unschedule = useCallback(
    async (taskId: number) => {
      const snapshot = data
      // Clear *both* the start and the due date: a remaining due_date would
      // back-schedule the task into a bar (see `resolveSpan`), so it'd never reach
      // the unscheduled bucket — which requires neither date. Optimistic so the bar
      // drops off the timeline immediately; `buildGanttModel` re-buckets it.
      setData((prev) =>
        prev === null
          ? prev
          : {
              ...prev,
              tasks: prev.tasks.map((t) =>
                t.id === taskId
                  ? { ...t, scheduled_start: null, due_date: null }
                  : t,
              ),
            },
      )
      try {
        await updateTask(taskId, { scheduled_start: null, due_date: null })
        notify('success', 'Task unscheduled')
        // Reconcile derived flags (blocked/blocking, conflict) and any cascade.
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to unschedule task'))
      }
    },
    [data, load, notify],
  )

  const resize = useCallback(
    async (taskId: number, newMinutes: number) => {
      const snapshot = data
      // Only leaf bars expose a resize handle (a parent's estimate is a server
      // rollup of its subtasks — see GanttChart `barResizable`), so this always
      // targets a directly-settable estimate.
      // Optimistic: re-estimate now so the resize feels instant. The
      // `buildGanttModel` memo in TimelinePage re-sizes the bar from this state.
      setData((prev) =>
        prev === null
          ? prev
          : {
              ...prev,
              tasks: prev.tasks.map((t) =>
                t.id === taskId ? { ...t, estimated_minutes: newMinutes } : t,
              ),
            },
      )
      try {
        await updateTask(taskId, { estimated_minutes: newMinutes })
        notify('success', 'Estimate updated')
        // Reconcile from the server: derived values (conflict outline,
        // blocked/blocking) *and* any downstream dependents the PATCH auto-shifted
        // (Slice 5 — a longer estimate pushes its end out, cascading the shift).
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to update estimate'))
      }
    },
    [data, load, notify],
  )

  const refetch = useCallback(() => load(), [load])

  return {
    data,
    loading: loadedId !== projectId,
    error,
    reschedule,
    unschedule,
    resize,
    refetch,
  }
}
