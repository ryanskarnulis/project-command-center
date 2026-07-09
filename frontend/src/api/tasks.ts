import { apiClient } from './client'
import type { Task, TaskCreate, TaskSeries, TaskUpdate } from '../types/task'

export async function listTasks(projectId: number): Promise<Task[]> {
  const res = await apiClient(`/api/projects/${projectId}/tasks`)
  return (await res.json()) as Task[]
}

// The all-tasks endpoint now caps its result server-side (default 500, max
// 1000). These views want the full working set, so they request the max
// explicitly rather than relying on the smaller default.
const MAX_TASK_LIMIT = 1000

export async function listAllTasks(): Promise<Task[]> {
  const res = await apiClient(`/api/tasks?limit=${MAX_TASK_LIMIT}`)
  return (await res.json()) as Task[]
}

export async function listCompletedTasks(projectId?: number): Promise<Task[]> {
  const path =
    projectId === undefined
      ? `/api/tasks?workflow_status=done&limit=${MAX_TASK_LIMIT}`
      : `/api/projects/${projectId}/tasks?workflow_status=done`
  const res = await apiClient(path)
  return (await res.json()) as Task[]
}

export async function createUnscopedTask(data: TaskCreate): Promise<Task> {
  const res = await apiClient('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as Task
}

export async function createTask(
  projectId: number,
  data: TaskCreate,
): Promise<Task> {
  const res = await apiClient(`/api/projects/${projectId}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as Task
}

export async function getTask(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}`)
  return (await res.json()) as Task
}

export async function updateTask(id: number, data: TaskUpdate): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as Task
}

export async function markTaskDone(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}/done`, { method: 'POST' })
  return (await res.json()) as Task
}

export async function skipOccurrence(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}/skip`, { method: 'POST' })
  return (await res.json()) as Task
}

/** Every occurrence in this task's recurrence series (oldest due date first). */
export async function getTaskSeries(id: number): Promise<TaskSeries> {
  const res = await apiClient(`/api/tasks/${id}/series`)
  return (await res.json()) as TaskSeries
}

/** Stop the series from spawning further occurrences (clears repeat_interval). */
export async function stopRecurrence(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}/stop-recurrence`, {
    method: 'POST',
  })
  return (await res.json()) as Task
}

export async function reopenTask(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}/reopen`, { method: 'POST' })
  return (await res.json()) as Task
}

export async function deleteTask(id: number): Promise<void> {
  await apiClient(`/api/tasks/${id}`, { method: 'DELETE' })
}

export async function restoreTask(id: number): Promise<Task> {
  const res = await apiClient(`/api/tasks/${id}/restore`, { method: 'POST' })
  return (await res.json()) as Task
}

export async function purgeTask(id: number): Promise<void> {
  await apiClient(`/api/tasks/${id}/purge`, { method: 'DELETE' })
}

export async function getSubtasks(id: number): Promise<Task[]> {
  const res = await apiClient(`/api/tasks/${id}/subtasks`)
  return (await res.json()) as Task[]
}
