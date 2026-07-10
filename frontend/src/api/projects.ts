import { apiClient } from './client'
import type {
  ActivityEvent,
  Project,
  ProjectCreate,
  ProjectUpdate,
} from '../types/project'

export async function listProjects(): Promise<Project[]> {
  const res = await apiClient('/api/projects')
  return (await res.json()) as Project[]
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
