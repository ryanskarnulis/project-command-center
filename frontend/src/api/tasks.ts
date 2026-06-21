import { apiClient } from './client'
import type {
  BreakdownReviewResult,
  SubtaskDecision,
  Task,
  TaskCreate,
  TaskUpdate,
} from '../types/task'

export async function listTasks(projectId: number): Promise<Task[]> {
  const res = await apiClient(`/api/projects/${projectId}/tasks`)
  return (await res.json()) as Task[]
}

export async function listAllTasks(): Promise<Task[]> {
  const res = await apiClient('/api/tasks')
  return (await res.json()) as Task[]
}

export async function listCompletedTasks(projectId?: number): Promise<Task[]> {
  const path =
    projectId === undefined
      ? '/api/tasks?workflow_status=done'
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

/** Ask the model to suggest subtasks; returns them as candidate children. */
export async function breakDownTask(id: number): Promise<Task[]> {
  const res = await apiClient(`/api/tasks/${id}/break-down`, { method: 'POST' })
  return (await res.json()) as Task[]
}

export async function reviewBreakdown(
  id: number,
  decisions: SubtaskDecision[],
): Promise<BreakdownReviewResult> {
  const res = await apiClient(`/api/tasks/${id}/breakdown/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  return (await res.json()) as BreakdownReviewResult
}
