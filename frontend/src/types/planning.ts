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
