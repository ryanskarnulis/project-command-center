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
