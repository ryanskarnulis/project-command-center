import { apiClient } from './client'
import type { Project, ProjectCreate, ProjectUpdate } from '../types/project'

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
