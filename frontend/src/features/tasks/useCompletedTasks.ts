import { useCallback, useEffect, useState } from 'react'
import { listCompletedTasks, reopenTask } from '../../api/tasks'
import type { Task } from '../../types/task'

interface UseCompletedTasks {
  tasks: Task[]
  loading: boolean
  error: string | null
  reopen: (id: number) => Promise<void>
  reload: () => void
}

/** Completed tasks as loaded, tagged with the scope that produced them. */
interface LoadedCompletedTasks {
  scope: number | undefined
  tasks: Task[]
}

/** An error tagged with the scope whose request produced it. */
interface ScopedError {
  scope: number | undefined
  message: string
}

const NO_TASKS: Task[] = []

function completedTasksKey(projectId: number | undefined, refreshKey: number): string {
  return JSON.stringify([projectId ?? null, refreshKey])
}

export function useCompletedTasks(
  projectId?: number,
  enabled = true,
): UseCompletedTasks {
  const [loaded, setLoaded] = useState<LoadedCompletedTasks | null>(null)
  const [scopedError, setScopedError] = useState<ScopedError | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])
  const requestKey = completedTasksKey(projectId, refreshKey)

  // Rows from another project must never render — or stay actionable — under
  // the current scope, so anything loaded for a different scope is derived away.
  const tasks = loaded !== null && loaded.scope === projectId ? loaded.tasks : NO_TASKS
  const error = scopedError !== null && scopedError.scope === projectId ? scopedError.message : null

  // Lazy: only fetch while enabled (e.g. the "Done" view is selected). Each time
  // it becomes enabled it refetches, so completed data is fresh when reopened.
  useEffect(() => {
    if (!enabled) return
    let active = true
    listCompletedTasks(projectId)
      .then((data) => {
        if (!active) return
        setLoaded({ scope: projectId, tasks: data })
        setScopedError(null)
        setLoadedKey(requestKey)
      })
      .catch((e: unknown) => {
        if (active) {
          setScopedError({
            scope: projectId,
            message: e instanceof Error ? e.message : 'Failed to load completed tasks',
          })
          setLoadedKey(requestKey)
        }
      })
    return () => {
      active = false
    }
  }, [projectId, requestKey, enabled])

  const reopen = useCallback(
    async (id: number) => {
      setScopedError(null)
      try {
        await reopenTask(id)
        // The task leaves the done list once reopened — drop it locally.
        setLoaded((prev) =>
          prev === null ? prev : { ...prev, tasks: prev.tasks.filter((t) => t.id !== id) },
        )
      } catch (e: unknown) {
        setScopedError({
          scope: projectId,
          message: e instanceof Error ? e.message : 'Failed to reopen task',
        })
        // Callers chain follow-up writes off a resolved reopen — a swallowed
        // rejection would let those run against a task that never reopened.
        throw e
      }
    },
    [projectId],
  )

  return { tasks, loading: enabled && loadedKey !== requestKey, error, reopen, reload }
}
