import { apiClient } from './client'
import type { SearchResults } from '../types/search'

export async function search(
  q: string,
  signal?: AbortSignal,
): Promise<SearchResults> {
  const res = await apiClient(`/api/search?q=${encodeURIComponent(q)}`, {
    signal,
  })
  return (await res.json()) as SearchResults
}
