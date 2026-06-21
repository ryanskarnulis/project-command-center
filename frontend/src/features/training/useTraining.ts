import { useCallback, useEffect, useState } from 'react'
import {
  deleteTrainingExample,
  getTrainingStats,
  listTrainingExamples,
  PAGE_SIZE,
} from '../../api/training'
import type {
  TrainingExample,
  TrainingFilters,
  TrainingStats,
} from '../../types/training'
import { ApiError } from '../../api/client'
import { useTrashCount } from '../trash/trashCountContext'

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
  loadingMore: boolean
  hasMore: boolean
  error: string | null
  filters: TrainingFilters
  setFilters: (filters: TrainingFilters) => void
  loadMore: () => void
  deleteExample: (id: number) => Promise<void>
}

export function useTraining(): UseTraining {
  const [stats, setStats] = useState<TrainingStats | null>(null)
  const [examples, setExamples] = useState<TrainingExample[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filters, setFilters] = useState<TrainingFilters>({})
  const { refresh: refreshTrashCount } = useTrashCount()

  // Stats are unfiltered (the corpus total drives the progress meter), so they
  // load once. The example list reloads whenever filters change.
  useEffect(() => {
    getTrainingStats()
      .then(setStats)
      .catch((err: unknown) => setError(errMessage(err)))
  }, [])

  // Filter change: load the first page from offset 0, replacing the list. A
  // full page (=== PAGE_SIZE) implies there may be more to fetch.
  useEffect(() => {
    listTrainingExamples(filters, PAGE_SIZE, 0)
      .then((page) => {
        setExamples(page)
        setHasMore(page.length === PAGE_SIZE)
      })
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

  // Append the next page from the current end of the list. Offset is the loaded
  // count rather than a page index so it stays correct regardless of filtering.
  const loadMore = useCallback(() => {
    setLoadingMore(true)
    listTrainingExamples(filters, PAGE_SIZE, examples.length)
      .then((page) => {
        setExamples((prev) => [...prev, ...page])
        setHasMore(page.length === PAGE_SIZE)
      })
      .catch((err: unknown) => setError(errMessage(err)))
      .finally(() => setLoadingMore(false))
  }, [filters, examples.length])

  // Move an example to trash: drop it from the loaded list, refresh the corpus
  // stats (so the goal meter falls), and refresh the sidebar trash badge. The
  // row is recoverable from the Trash page until purged.
  const deleteExample = useCallback(
    async (id: number) => {
      setError(null)
      try {
        await deleteTrainingExample(id)
        setExamples((prev) => prev.filter((e) => e.id !== id))
        getTrainingStats().then(setStats).catch(() => {})
        void refreshTrashCount()
      } catch (err: unknown) {
        setError(errMessage(err))
      }
    },
    [refreshTrashCount],
  )

  return {
    stats,
    examples,
    loading,
    loadingMore,
    hasMore,
    error,
    filters,
    setFilters: updateFilters,
    loadMore,
    deleteExample,
  }
}
