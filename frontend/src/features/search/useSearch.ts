import { useEffect, useState } from 'react'
import { search } from '../../api/search'
import type { SearchResults } from '../../types/search'

const EMPTY: SearchResults = { projects: [], tasks: [], inbox_items: [] }

interface UseSearch {
  results: SearchResults
  loading: boolean
  error: string | null
  /** Total matches across all groups — drives the empty state. */
  total: number
}

/**
 * Debounced global search. Re-queries when `query` settles; a blank query resets to
 * empty without hitting the API. In-flight requests are aborted when the query
 * changes so a slow earlier response can't overwrite a newer one.
 */
export function useSearch(query: string, debounceMs = 200): UseSearch {
  const [results, setResults] = useState<SearchResults>(EMPTY)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed === '') {
      setResults(EMPTY)
      setLoading(false)
      setError(null)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    const timer = setTimeout(() => {
      search(trimmed, controller.signal)
        .then((data) => {
          setResults(data)
          setError(null)
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return
          setError(e instanceof Error ? e.message : 'Search failed')
          setResults(EMPTY)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, debounceMs)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query, debounceMs])

  const total =
    results.projects.length + results.tasks.length + results.inbox_items.length

  return { results, loading, error, total }
}
