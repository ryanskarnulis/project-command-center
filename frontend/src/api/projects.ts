import { apiClient } from './client'
import type {
  ActivityEvent,
  Project,
  ProjectCreate,
  ProjectUpdate,
} from '../types/project'

export async function listProjects(includeClosed = false): Promise<Project[]> {
  const query = includeClosed ? '?include_closed=true' : ''
  return apiClient<Project[]>(`/api/projects${query}`)
}

export async function closeProject(id: number): Promise<Project> {
  return apiClient<Project>(`/api/projects/${id}/close`, { method: 'POST' })
}

export async function reopenProject(id: number): Promise<Project> {
  return apiClient<Project>(`/api/projects/${id}/reopen`, { method: 'POST' })
}

export async function getProject(id: number): Promise<Project> {
  return apiClient<Project>(`/api/projects/${id}`)
}

export async function createProject(data: ProjectCreate): Promise<Project> {
  return apiClient<Project>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function updateProject(
  id: number,
  data: ProjectUpdate,
): Promise<Project> {
  return apiClient<Project>(`/api/projects/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

/** Full manual order: every active project id, in display order. */
export async function reorderProjects(projectIds: number[]): Promise<Project[]> {
  return apiClient<Project[]>('/api/projects/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_ids: projectIds }),
  })
}

export async function deleteProject(id: number): Promise<void> {
  await apiClient(`/api/projects/${id}`, { method: 'DELETE' })
}

export interface ProjectRestoreResult {
  project: Project
  restored_task_count: number
}

export async function restoreProject(
  id: number,
  restoreTasks = false,
): Promise<ProjectRestoreResult> {
  const query = restoreTasks ? '?restore_tasks=true' : ''
  return apiClient<ProjectRestoreResult>(`/api/projects/${id}/restore${query}`, {
    method: 'POST',
  })
}

export async function purgeProject(id: number): Promise<void> {
  await apiClient(`/api/projects/${id}/purge`, { method: 'DELETE' })
}

export async function getProjectActivity(
  id: number,
  limit = 50,
): Promise<ActivityEvent[]> {
  return apiClient<ActivityEvent[]>(`/api/projects/${id}/activity?limit=${limit}`)
}
