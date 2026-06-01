import { useCallback, useEffect, useState } from 'react'
import { getDashboard, getProjectSummary } from '../../api/dashboard'
import type { DashboardOverview, ProjectSummary } from '../../types/dashboard'
import { ApiError } from '../../api/client'

interface SummaryState {
  loading: boolean
  data: ProjectSummary | null
  error: string | null
}

interface UseDashboard {
  overview: DashboardOverview | null
  loading: boolean
  error: string | null
  summaries: Record<number, SummaryState>
  summarize: (projectId: number) => void
}

export function useDashboard(): UseDashboard {
  const [overview, setOverview] = useState<DashboardOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [summaries, setSummaries] = useState<Record<number, SummaryState>>({})

  useEffect(() => {
    getDashboard()
      .then(setOverview)
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Failed to load dashboard')
      })
      .finally(() => setLoading(false))
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

  return { overview, loading, error, summaries, summarize }
}
