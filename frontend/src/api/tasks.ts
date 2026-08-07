import { apiClient } from './client'
import type { Task, TaskCreate, TaskSeries, TaskUpdate } from '../types/task'

export async function listTasks(projectId: number): Promise<Task[]> {
  return apiClient<Task[]>(`/api/projects/${projectId}/tasks`)
}

// The all-tasks endpoint caps its result server-side (default 500, max 1000).
// The "all tasks" views want the full working set, so they page through it: a
// single max-size request would silently drop every task past the first 1,000.
const MAX_TASK_LIMIT = 1000

/**
 * Fetch every page of a capped list endpoint, following `offset` until a short
 * page signals the end. Sequential paged reads (not N+1) over the server's
 * already-supported offset paging — the only way to read past the 1,000 cap.
 */
async function fetchAllPages(
  buildPath: (limit: number, offset: number) => string,
): Promise<Task[]> {
  const all: Task[] = []
  for (let offset = 0; ; offset += MAX_TASK_LIMIT) {
    const page = await apiClient<Task[]>(buildPath(MAX_TASK_LIMIT, offset))
    all.push(...page)
    if (page.length < MAX_TASK_LIMIT) break
  }
  return all
}

export async function listAllTasks(): Promise<Task[]> {
  return fetchAllPages((limit, offset) => `/api/tasks?limit=${limit}&offset=${offset}`)
}

export async function listCompletedTasks(projectId?: number): Promise<Task[]> {
  // The project-scoped route is unbounded (no cap), so it needs no paging; only
  // the global completed list rides the capped /api/tasks endpoint.
  if (projectId !== undefined) {
    return apiClient<Task[]>(`/api/projects/${projectId}/tasks?workflow_status=done`)
  }
  return fetchAllPages(
    (limit, offset) =>
      `/api/tasks?workflow_status=done&limit=${limit}&offset=${offset}`,
  )
}

export async function createUnscopedTask(data: TaskCreate): Promise<Task> {
  return apiClient<Task>('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function createTask(
  projectId: number,
  data: TaskCreate,
): Promise<Task> {
  return apiClient<Task>(`/api/projects/${projectId}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function getTask(id: number): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}`)
}

export async function updateTask(id: number, data: TaskUpdate): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function markTaskDone(id: number): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}/done`, { method: 'POST' })
}

export async function skipOccurrence(id: number): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}/skip`, { method: 'POST' })
}

/** Every occurrence in this task's recurrence series (oldest due date first). */
export async function getTaskSeries(id: number): Promise<TaskSeries> {
  return apiClient<TaskSeries>(`/api/tasks/${id}/series`)
}

/** Stop the series from spawning further occurrences (clears repeat_interval). */
export async function stopRecurrence(id: number): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}/stop-recurrence`, {
    method: 'POST',
  })
}

export async function reopenTask(id: number): Promise<Task> {
  return apiClient<Task>(`/api/tasks/${id}/reopen`, { method: 'POST' })
}

export async function deleteTask(id: number): Promise<void> {
  await apiClient(`/api/tasks/${id}`, { method: 'DELETE' })
}

/** Restore a trashed task. With `restoreSubtasks`, also restores exactly the
 * subtasks that were cascade-trashed with it (the inverse of one delete). */
export async function restoreTask(
  id: number,
  restoreSubtasks = false,
): Promise<Task> {
  const query = restoreSubtasks ? '?restore_subtasks=true' : ''
  return apiClient<Task>(`/api/tasks/${id}/restore${query}`, { method: 'POST' })
}

export async function purgeTask(id: number): Promise<void> {
  await apiClient(`/api/tasks/${id}/purge`, { method: 'DELETE' })
}

export async function getSubtasks(id: number): Promise<Task[]> {
  return apiClient<Task[]>(`/api/tasks/${id}/subtasks`)
}
