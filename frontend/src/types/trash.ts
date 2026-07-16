import type { Project } from './project'
import type { Task } from './task'

export interface TrashProject extends Project {
  /** Tasks cascade-deleted with this project that would return if restored with it. */
  archived_task_count: number
}

export interface Trash {
  projects: TrashProject[]
  tasks: Task[]
}

export interface EmptyTrashResult {
  projects: number
  tasks: number
}

export interface PurgeSelectedRequest {
  project_ids: number[]
  task_ids: number[]
}

export interface TrashCountResult {
  projects: number
  tasks: number
}
