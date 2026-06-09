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

export function useCompletedTasks(
  projectId?: number,
  enabled = true,
): UseCompletedTasks {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  // Lazy: only fetch while enabled (e.g. the "Done" view is selected). Each time
  // it becomes enabled it refetches, so completed data is fresh when reopened.
  useEffect(() => {
    if (!enabled) return
    let active = true
    setLoading(true)
    listCompletedTasks(projectId)
      .then((data) => {
        if (!active) return
        setTasks(data)
        setError(null)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load completed tasks')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [projectId, refreshKey, enabled])

  const reopen = useCallback(async (id: number) => {
    setError(null)
    try {
      await reopenTask(id)
      // The task leaves the done list once reopened — drop it locally.
      setTasks((prev) => prev.filter((t) => t.id !== id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to reopen task')
    }
  }, [])

  return { tasks, loading, error, reopen, reload }
}
