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
        // Reconcile derived flags (conflict outline, blocked/blocking) from the
        // server. No downstream auto-shift — that is a later slice.
        load()
      } catch (err: unknown) {
        setData(snapshot)
        notify('error', errorMessage(err, 'Failed to reschedule task'))
      }
    },
    [data, load, notify],
  )

  return { data, loading: loadedId !== projectId, error, reschedule }
}
