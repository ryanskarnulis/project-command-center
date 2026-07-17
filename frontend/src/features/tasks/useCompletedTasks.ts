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

function completedTasksKey(projectId: number | undefined, refreshKey: number): string {
  return JSON.stringify([projectId ?? null, refreshKey])
}

export function useCompletedTasks(
  projectId?: number,
  enabled = true,
): UseCompletedTasks {
  const [tasks, setTasks] = useState<Task[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])
  const requestKey = completedTasksKey(projectId, refreshKey)

  // Lazy: only fetch while enabled (e.g. the "Done" view is selected). Each time
  // it becomes enabled it refetches, so completed data is fresh when reopened.
  useEffect(() => {
    if (!enabled) return
    let active = true
    listCompletedTasks(projectId)
      .then((data) => {
        if (!active) return
        setTasks(data)
        setError(null)
        setLoadedKey(requestKey)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load completed tasks')
          setLoadedKey(requestKey)
        }
      })
    return () => {
      active = false
    }
  }, [projectId, requestKey, enabled])

  const reopen = useCallback(async (id: number) => {
    setError(null)
    try {
      await reopenTask(id)
      // The task leaves the done list once reopened — drop it locally.
      setTasks((prev) => prev.filter((t) => t.id !== id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to reopen task')
      // Callers chain follow-up writes off a resolved reopen — a swallowed
      // rejection would let those run against a task that never reopened.
      throw e
    }
  }, [])

  return { tasks, loading: enabled && loadedKey !== requestKey, error, reopen, reload }
}
