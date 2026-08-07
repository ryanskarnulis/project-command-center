import { apiClient } from './client'
import type { TaskDependency, TaskDependent } from '../types/task'

export async function listDependencies(
  taskId: number,
): Promise<TaskDependency[]> {
  return apiClient<TaskDependency[]>(`/api/tasks/${taskId}/dependencies`)
}

export async function listDependents(taskId: number): Promise<TaskDependent[]> {
  return apiClient<TaskDependent[]>(`/api/tasks/${taskId}/dependents`)
}

export async function addDependency(
  taskId: number,
  dependsOnTaskId: number,
): Promise<TaskDependency> {
  return apiClient<TaskDependency>(`/api/tasks/${taskId}/dependencies`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ depends_on_task_id: dependsOnTaskId }),
  })
}

export async function removeDependency(
  taskId: number,
  dependencyId: number,
): Promise<void> {
  await apiClient(`/api/tasks/${taskId}/dependencies/${dependencyId}`, {
    method: 'DELETE',
  })
}
