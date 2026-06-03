import { apiClient } from './client'
import type { TaskDependency } from '../types/task'

export async function listDependencies(
  taskId: number,
): Promise<TaskDependency[]> {
  const res = await apiClient(`/api/tasks/${taskId}/dependencies`)
  return (await res.json()) as TaskDependency[]
}

export async function addDependency(
  taskId: number,
  dependsOnTaskId: number,
): Promise<TaskDependency> {
  const res = await apiClient(`/api/tasks/${taskId}/dependencies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  })
  return (await res.json()) as TaskDependency
}

export async function removeDependency(
  taskId: number,
  dependencyId: number,
): Promise<void> {
  await apiClient(`/api/tasks/${taskId}/dependencies/${dependencyId}`, {
    method: 'DELETE',
  })
}
