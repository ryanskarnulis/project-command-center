import { apiClient } from './client'
import type {
  TrainingExample,
  TrainingFilters,
  TrainingStats,
} from '../types/training'

export async function getTrainingStats(): Promise<TrainingStats> {
  const res = await apiClient('/api/training-examples/stats')
  return (await res.json()) as TrainingStats
}

export async function listTrainingExamples(
  filters: TrainingFilters = {},
  limit = 50,
): Promise<TrainingExample[]> {
  const params = new URLSearchParams()
  if (filters.task_name) params.set('task_name', filters.task_name)
  if (filters.accepted !== undefined) params.set('accepted', String(filters.accepted))
  params.set('limit', String(limit))
  const res = await apiClient(`/api/training-examples?${params.toString()}`)
  return (await res.json()) as TrainingExample[]
}
