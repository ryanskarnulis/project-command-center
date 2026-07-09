import { useCallback, useEffect, useRef, useState } from 'react'
import { getDashboard } from '../../api/dashboard'
import { listAllTasks } from '../../api/tasks'
import type { DashboardOverview } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { useTaskRefresh } from '../tasks/taskRefreshContext'

interface UseDashboard {
  overview: DashboardOverview | null
  tasks: Task[]
  /** True only during the initial load; a background refetch uses `refreshing`. */
  loading: boolean
  /** True while a reload/cross-page refetch is in flight after the first load. */
  refreshing: boolean
  error: string | null
  reload: () => void
}

export function useDashboard(): UseDashboard {
  const [overview, setOverview] = useState<DashboardOverview | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  // Once the first load resolves we never flip `loading` back on — subsequent
  // refetches surface through `refreshing` so the page doesn't flash a spinner.
  const hasLoaded = useRef(false)
  // Mutations outside this hook (e.g. sidebar drag-to-file) refetch off this.
  const { version: taskRefreshVersion } = useTaskRefresh()

  const reload = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    let active = true
    if (hasLoaded.current) setRefreshing(true)
    Promise.all([getDashboard(), listAllTasks()])
      .then(([overview, tasks]) => {
        if (!active) return
        setOverview(overview)
        setTasks(tasks)
        setError(null)
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Failed to load dashboard')
        }
      })
      .finally(() => {
        if (!active) return
        hasLoaded.current = true
        setLoading(false)
        setRefreshing(false)
      })
    return () => {
      active = false
    }
  }, [refreshKey, taskRefreshVersion])

  return {
    overview,
    tasks,
    loading,
    refreshing,
    error,
    reload,
  }
}
