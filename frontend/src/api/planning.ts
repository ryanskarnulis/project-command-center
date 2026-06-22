import { apiClient } from './client'
import type {
  GlobalGantt,
  ProjectGantt,
  WhatIfOverride,
  WhatIfResult,
} from '../types/planning'

/**
 * Fetch the read-only planning payload for a project: accepted, not-done tasks
 * plus the dependency edges between them. Bar geometry is derived client-side in
 * `features/planning/ganttModel`.
 */
export async function getProjectGantt(projectId: number): Promise<ProjectGantt> {
  const res = await apiClient(`/api/projects/${projectId}/gantt`)
  return (await res.json()) as ProjectGantt
}

/**
 * Fetch the cross-project planning payload (Slice 8): accepted, not-done tasks
 * across every project, the edges among them (which may cross projects), and the
 * projects each belongs to for grouping/coloring. Bar geometry is derived
 * client-side in `features/planning/ganttModel`, same as the per-project read.
 */
export async function getGlobalGantt(): Promise<GlobalGantt> {
  const res = await apiClient('/api/planning/gantt')
  return (await res.json()) as GlobalGantt
}

/**
 * Preview a staged schedule change without saving it. The backend runs the same
 * `compute_shifts` the committed PATCH cascade uses over a hypothetical placement
 * set and returns the resulting starts (the overrides plus cascaded dependents) —
 * nothing is persisted. The scheduling math stays in Python (prime directive #1);
 * the frontend only renders the returned dates.
 */
export async function previewWhatIf(
  projectId: number,
  overrides: WhatIfOverride[],
): Promise<WhatIfResult> {
  const res = await apiClient(`/api/projects/${projectId}/gantt/what-if`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ overrides }),
  })
  return (await res.json()) as WhatIfResult
}
