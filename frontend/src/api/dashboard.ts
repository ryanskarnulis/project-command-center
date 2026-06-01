import { apiClient } from './client'
import type { DashboardOverview, ProjectSummary } from '../types/dashboard'

export async function getDashboard(): Promise<DashboardOverview> {
  const res = await apiClient('/api/dashboard')
  return (await res.json()) as DashboardOverview
}

export async function getProjectSummary(projectId: number): Promise<ProjectSummary> {
  const res = await apiClient(`/api/projects/${projectId}/summary`)
  return (await res.json()) as ProjectSummary
}
