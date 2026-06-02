import { useCallback, useEffect, useState } from 'react'
import { getTrainingStats, listTrainingExamples } from '../../api/training'
import type {
  TrainingExample,
  TrainingFilters,
  TrainingStats,
} from '../../types/training'
import { ApiError } from '../../api/client'

function errMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: string } | null)?.detail
    return detail ?? `Error ${err.status}`
  }
  if (err instanceof Error) return err.message
  return 'Unknown error'
}

interface UseTraining {
  stats: TrainingStats | null
  examples: TrainingExample[]
  loading: boolean
  error: string | null
  filters: TrainingFilters
  setFilters: (filters: TrainingFilters) => void
}

export function useTraining(): UseTraining {
  const [stats, setStats] = useState<TrainingStats | null>(null)
  const [examples, setExamples] = useState<TrainingExample[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filters, setFilters] = useState<TrainingFilters>({})

  // Stats are unfiltered (the corpus total drives the progress meter), so they
  // load once. The example list reloads whenever filters change.
  useEffect(() => {
    getTrainingStats()
      .then(setStats)
      .catch((err: unknown) => setError(errMessage(err)))
  }, [])

  useEffect(() => {
    listTrainingExamples(filters)
      .then(setExamples)
      .catch((err: unknown) => setError(errMessage(err)))
      .finally(() => setLoading(false))
  }, [filters])

  // Set loading in the handler (not the effect) so a filter change re-shows the
  // loading state without triggering a synchronous setState inside the effect.
  const updateFilters = useCallback((next: TrainingFilters) => {
    setError(null)
    setLoading(true)
    setFilters(next)
  }, [])

  return { stats, examples, loading, error, filters, setFilters: updateFilters }
}
