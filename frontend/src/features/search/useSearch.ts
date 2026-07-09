import { useEffect, useState } from 'react'
import { search } from '../../api/search'
import type { SearchResults } from '../../types/search'

const EMPTY: SearchResults = { projects: [], tasks: [] }

interface UseSearch {
  results: SearchResults
  loading: boolean
  error: string | null
  /** Total matches across all groups — drives the empty state. */
  total: number
}

interface SearchState {
  query: string
  results: SearchResults
  loading: boolean
  error: string | null
}

/**
 * Debounced global search. Re-queries when `query` settles; a blank query resets to
 * empty without hitting the API. In-flight requests are aborted when the query
 * changes so a slow earlier response can't overwrite a newer one.
 */
export function useSearch(query: string, debounceMs = 200): UseSearch {
  const [state, setState] = useState<SearchState>({
    query: '',
    results: EMPTY,
    loading: false,
    error: null,
  })

  useEffect(() => {
    const trimmed = query.trim()
    if (trimmed === '') {
      return
    }

    const controller = new AbortController()
    const timer = setTimeout(() => {
      search(trimmed, controller.signal)
        .then((data) => {
          setState({
            query: trimmed,
            results: data,
            loading: false,
            error: null,
          })
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return
          setState({
            query: trimmed,
            results: EMPTY,
            loading: false,
            error: e instanceof Error ? e.message : 'Search failed',
          })
        })
    }, debounceMs)

    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query, debounceMs])

  const trimmed = query.trim()
  const isBlank = trimmed === ''
  const isCurrent = state.query === trimmed
  const results = isBlank || !isCurrent ? EMPTY : state.results
  const loading = !isBlank && (!isCurrent || state.loading)
  const error = isBlank || !isCurrent ? null : state.error
  const total = results.projects.length + results.tasks.length

  return { results, loading, error, total }
}
