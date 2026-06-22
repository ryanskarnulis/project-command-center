import { apiClient } from './client'
import type { ProjectGantt } from '../types/planning'

/**
 * Fetch the read-only planning payload for a project: accepted, not-done tasks
 * plus the dependency edges between them. Bar geometry is derived client-side in
 * `features/planning/ganttModel`.
 */
export async function getProjectGantt(projectId: number): Promise<ProjectGantt> {
  const res = await apiClient(`/api/projects/${projectId}/gantt`)
  return (await res.json()) as ProjectGantt
}
