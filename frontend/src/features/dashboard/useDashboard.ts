import { useCallback, useEffect, useState } from 'react'
import { getDashboard, getProjectSummary } from '../../api/dashboard'
import { listAllTasks } from '../../api/tasks'
import type { DashboardOverview, ProjectSummary } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { ApiError } from '../../api/client'

interface SummaryState {
  loading: boolean
  data: ProjectSummary | null
  error: string | null
}

interface UseDashboard {
  overview: DashboardOverview | null
  tasks: Task[]
  loading: boolean
  error: string | null
  summaries: Record<number, SummaryState>
  summarize: (projectId: number) => void
}

export function useDashboard(): UseDashboard {
  const [overview, setOverview] = useState<DashboardOverview | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [summaries, setSummaries] = useState<Record<number, SummaryState>>({})

  useEffect(() => {
    let active = true
    Promise.all([getDashboard(), listAllTasks()])
      .then(([overview, tasks]) => {
        if (!active) return
        setOverview(overview)
        setTasks(tasks)
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Failed to load dashboard')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const summarize = useCallback((projectId: number) => {
    setSummaries((prev) => ({
      ...prev,
      [projectId]: { loading: true, data: null, error: null },
    }))
    getProjectSummary(projectId)
      .then((data) => {
        setSummaries((prev) => ({
          ...prev,
          [projectId]: { loading: false, data, error: null },
        }))
      })
      .catch((err: unknown) => {
        const message =
          err instanceof ApiError
            ? (err.body as { detail?: string })?.detail ?? `Error ${err.status}`
            : err instanceof Error
              ? err.message
              : 'Unknown error'
        setSummaries((prev) => ({
          ...prev,
          [projectId]: { loading: false, data: null, error: message },
        }))
      })
  }, [])

  return {
    overview,
    tasks,
    loading,
    error,
    summaries,
    summarize,
  }
}
