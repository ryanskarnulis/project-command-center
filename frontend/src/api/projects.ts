import { apiClient } from './client'
import type {
  ActivityEvent,
  Project,
  ProjectCreate,
  ProjectUpdate,
} from '../types/project'

export async function listProjects(includeClosed = false): Promise<Project[]> {
  const query = includeClosed ? '?include_closed=true' : ''
  const res = await apiClient(`/api/projects${query}`)
  return (await res.json()) as Project[]
}

export async function closeProject(id: number): Promise<Project> {
  const res = await apiClient(`/api/projects/${id}/close`, { method: 'POST' })
  return (await res.json()) as Project
}

export async function reopenProject(id: number): Promise<Project> {
  const res = await apiClient(`/api/projects/${id}/reopen`, { method: 'POST' })
  return (await res.json()) as Project
}

export async function getProject(id: number): Promise<Project> {
  const res = await apiClient(`/api/projects/${id}`)
  return (await res.json()) as Project
}

export async function createProject(data: ProjectCreate): Promise<Project> {
  const res = await apiClient('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as Project
}

export async function updateProject(
  id: number,
  data: ProjectUpdate,
): Promise<Project> {
  const res = await apiClient(`/api/projects/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as Project
}

/** Full manual order: every active project id, in display order. */
export async function reorderProjects(projectIds: number[]): Promise<Project[]> {
  const res = await apiClient('/api/projects/order', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_ids: projectIds }),
  })
  return (await res.json()) as Project[]
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
  const res = await apiClient(`/api/projects/${id}/restore${query}`, { method: 'POST' })
  return (await res.json()) as ProjectRestoreResult
}

export async function purgeProject(id: number): Promise<void> {
  await apiClient(`/api/projects/${id}/purge`, { method: 'DELETE' })
}

export async function getProjectActivity(
  id: number,
  limit = 50,
): Promise<ActivityEvent[]> {
  const res = await apiClient(`/api/projects/${id}/activity?limit=${limit}`)
  return (await res.json()) as ActivityEvent[]
}
