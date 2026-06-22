import type { Task } from './task'

/** A flat `task_id depends_on depends_on_task_id` edge (mirrors the backend). */
export interface DependencyEdge {
  task_id: number
  depends_on_task_id: number
}

/** The per-project planning payload: tasks plus the edges between them. */
export interface ProjectGantt {
  tasks: Task[]
  dependencies: DependencyEdge[]
}

/** A project's identity for grouping/coloring bars on the global timeline. */
export interface GanttProject {
  id: number
  name: string
}

/**
 * The cross-project planning payload (Slice 8): every project's scheduled work
 * on one axis. A superset of `ProjectGantt` — the same tasks + edges (here
 * spanning all projects, so edges may cross project boundaries) plus the
 * `projects` each task belongs to, for the grouped, colored layout.
 */
export interface GlobalGantt {
  tasks: Task[]
  dependencies: DependencyEdge[]
  projects: GanttProject[]
}

/** A staged, unsaved placement change to preview (mirrors the backend). */
export interface WhatIfOverride {
  task_id: number
  scheduled_start?: string | null
  estimated_minutes?: number | null
}

/** A task's previewed `scheduled_start` under the staged overrides. */
export interface WhatIfShift {
  task_id: number
  scheduled_start: string
}

/** The hypothetical schedule: every task that ends up on a different day. */
export interface WhatIfResult {
  shifts: WhatIfShift[]
}
