import type { InboxItem } from './inbox'
import type { Project } from './project'
import type { Task } from './task'
import type { TrainingExample } from './training'

export interface TrashProject extends Project {
  /** Tasks cascade-deleted with this project that would return if restored with it. */
  archived_task_count: number
}

export interface Trash {
  projects: TrashProject[]
  tasks: Task[]
  inbox_items: InboxItem[]
  training_examples: TrainingExample[]
}

export interface EmptyTrashResult {
  projects: number
  tasks: number
  inbox_items: number
  training_examples: number
}

export interface TrashCountResult {
  projects: number
  tasks: number
  inbox_items: number
  training_examples: number
}
