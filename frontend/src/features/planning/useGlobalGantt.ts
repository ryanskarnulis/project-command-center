import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../../api/client'
import { getGlobalGantt } from '../../api/planning'
import { updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { GlobalGantt } from '../../types/planning'

interface UseGlobalGantt {
  data: GlobalGantt | null
  loading: boolean
  error: string | null
  /** Optimistically move a task's bar, persist via PATCH, revert on error. */
  reschedule: (taskId: number, newStart: string) => Promise<void>
  /** Optimistically resize a task's estimate, persist via PATCH, revert on error. */
  resize: (taskId: number, newMinutes: number) => Promise<void>
  /** Re-fetch the cross-project payload (e.g. to surface a cascaded shift). */
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
 * The cross-project twin of `useProjectGantt` (Slice 8): owns the global planning
 * payload and the same optimistic drag-to-reschedule / resize mutations. Identical
 * mutation shape — the only difference is the data is `GlobalGantt` (tasks span
 * every project, plus a `projects` legend) and there is no project id to scope to.
 * The PATCH cascade now spans projects server-side, so the post-success refetch
 * surfaces a cross-project dependent that shifted (CLAUDE.md prime directive #1 —
 * the scheduling math stays in Python; the frontend just refetches).
 */
export function useGlobalGantt(): UseGlobalGantt {
  const [data, setData] = useState<GlobalGantt | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const { notify } = useToast()

  const load = useCallback((active: () => boolean = () => true) => {
    getGlobalGantt()
      .then((result) => {
        if (!active()) return
        setData(result)
        setError(null)
        setLoaded(true)
      })
      .catch((err: unknown) => {
        if (!active()) return
        setError(errorMessage(err, 'Failed to load planning'))
        setLoaded(true)
      })
  }, [])

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
        // Reconcile: derived flags *and* any (possibly cross-project) dependents the
        // PATCH auto-shifted server-side.
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to reschedule task'))
      }
    },
    [data, load, notify],
  )

  const resize = useCallback(
    async (taskId: number, newMinutes: number) => {
      const snapshot = data
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
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to update estimate'))
      }
    },
    [data, load, notify],
  )

  const refetch = useCallback(() => load(), [load])

  return { data, loading: !loaded, error, reschedule, resize, refetch }
}
