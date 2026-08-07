import { apiClient } from './client'
import type { SearchResults } from '../types/search'

export async function search(
  q: string,
  signal?: AbortSignal,
): Promise<SearchResults> {
  return apiClient<SearchResults>(`/api/search?q=${encodeURIComponent(q)}`, {
    signal,
  })
}
