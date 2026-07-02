import { AI_TIMEOUT_MS, apiClient } from './client'
import type { DashboardOverview, ProjectSummary } from '../types/dashboard'

export async function getDashboard(): Promise<DashboardOverview> {
  const res = await apiClient('/api/dashboard')
  return (await res.json()) as DashboardOverview
}

export async function getProjectSummary(projectId: number): Promise<ProjectSummary> {
  const res = await apiClient(`/api/projects/${projectId}/summary`, {
    timeoutMs: AI_TIMEOUT_MS,
  })
  return (await res.json()) as ProjectSummary
}
