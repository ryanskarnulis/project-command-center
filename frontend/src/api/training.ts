import { apiClient } from './client'
import type {
  TrainingExample,
  TrainingFilters,
  TrainingStats,
} from '../types/training'

/** Page size for the example list. Single source of truth: the hook derives
 *  `hasMore` from whether a page came back full, so both must agree. */
export const PAGE_SIZE = 50

export async function getTrainingStats(): Promise<TrainingStats> {
  const res = await apiClient('/api/training-examples/stats')
  return (await res.json()) as TrainingStats
}

export async function listTrainingExamples(
  filters: TrainingFilters = {},
  limit = PAGE_SIZE,
  offset = 0,
): Promise<TrainingExample[]> {
  const params = new URLSearchParams()
  if (filters.task_name) params.set('task_name', filters.task_name)
  if (filters.status) params.set('status', filters.status)
  if (filters.model_profile) params.set('model_profile', filters.model_profile)
  if (filters.search) params.set('search', filters.search)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  const res = await apiClient(`/api/training-examples?${params.toString()}`)
  return (await res.json()) as TrainingExample[]
}

/** Soft-delete an example: move it to trash (reversible). */
export async function deleteTrainingExample(id: number): Promise<void> {
  await apiClient(`/api/training-examples/${id}`, { method: 'DELETE' })
}

/** Restore a trashed example back into the corpus. */
export async function restoreTrainingExample(id: number): Promise<TrainingExample> {
  const res = await apiClient(`/api/training-examples/${id}/restore`, { method: 'POST' })
  return (await res.json()) as TrainingExample
}

/** Permanently delete a trashed example (irreversible). */
export async function purgeTrainingExample(id: number): Promise<void> {
  await apiClient(`/api/training-examples/${id}/purge`, { method: 'DELETE' })
}
