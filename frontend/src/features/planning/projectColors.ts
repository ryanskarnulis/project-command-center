import type { GanttProject } from '../../types/planning'

// Deterministic per-project colors for the global timeline (Slice 8). The palette
// lives in code, not the DB — color is presentation, derived from a project's
// position in the (id-ordered) payload so the same project always reads the same
// hue across the chart bars and the legend. No scheduling logic here.

/** The cycling accent palette; index = project order in the payload. */
export const PROJECT_PALETTE = [
  '#3b82f6', // blue
  '#10b981', // green
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#14b8a6', // teal
  '#ef4444', // red
  '#6366f1', // indigo
] as const

/** The accent color for the project at `index` in the payload's project order. */
export function projectColor(index: number): string {
  return PROJECT_PALETTE[index % PROJECT_PALETTE.length]
}

/** `project_id -> accent color`, assigned by the project's order in the payload. */
export function projectColorMap(projects: GanttProject[]): Map<number, string> {
  return new Map(projects.map((p, i) => [p.id, projectColor(i)]))
}
