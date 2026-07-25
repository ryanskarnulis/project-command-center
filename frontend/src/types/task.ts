export type TaskWorkflowStatus = 'open' | 'in_progress' | 'done'
export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'
export type RepeatUnit = 'day' | 'week' | 'month'

/** A recurrence cadence, e.g. `{ unit: 'week', every: 2 }` (every 2 weeks). */
export interface RepeatInterval {
  unit: RepeatUnit
  every: number
}

export type EditScope = 'this' | 'future'

export interface Task {
  id: number
  project_id: number | null
  parent_task_id: number | null
  title: string
  description: string | null
  workflow_status: TaskWorkflowStatus
  priority: TaskPriority
  due_date: string | null
  // Day-plan snooze (Focus page defer): the scheduler skips the task while
  // this date is in the future. Null = not deferred.
  deferred_until: string | null
  estimated_minutes: number | null
  repeat_interval: RepeatInterval | null
  recurrence_id: string | null
  // Derived server-side: due date of the next occurrence for an open recurring
  // task, shown as "next <date>" beside the repeat badge. Null when the task is
  // not recurring or already done.
  next_occurrence_date: string | null
  created_at: string
  updated_at: string
  deleted_at?: string | null
  // Derived server-side: true while any dependency is unfinished.
  is_blocked: boolean
  // Derived server-side: true when this is the top active blocker for downstream work.
  is_blocking: boolean
  // Derived server-side: transitive count of unfinished tasks waiting on it.
  blocked_task_count: number
  // Derived server-side: true when the task has subtasks. When true,
  // `estimated_minutes` and `workflow_status` carry rolled-up values and are
  // read-only (set them by editing the subtasks instead).
  has_subtasks: boolean
  // Derived server-side: true when the task behaves as a root — no parent, or a
  // parent that is trashed/purged (an orphan is promoted). Flat surfaces filter
  // roots on this rather than `parent_task_id === null`. Optional only so
  // locally-constructed tasks stay assignable; the API always sends it. Read it
  // through `isEffectiveTopLevel` (features/tasks/taskTree), never directly.
  is_effective_top_level?: boolean
}

/** A recurrence series: every occurrence sharing a `recurrence_id`. */
export interface TaskSeries {
  recurrence_id: string
  // Oldest due date first. Active and skipped occurrences only: a skipped one is
  // soft-deleted and carries a non-null `deleted_at`, while a normally-trashed
  // occurrence is omitted entirely — so `deleted_at` here means "skipped".
  occurrences: Task[]
}

export interface TaskDependency {
  id: number
  task_id: number
  depends_on_task_id: number
  depends_on_title: string
  depends_on_workflow_status: TaskWorkflowStatus
  depends_on_done: boolean
}

export interface TaskDependent {
  id: number
  task_id: number
  dependent_task_id: number
  dependent_title: string
  dependent_workflow_status: TaskWorkflowStatus
  dependent_done: boolean
}

export interface TaskCreate {
  title: string
  description?: string | null
  workflow_status?: TaskWorkflowStatus
  priority?: TaskPriority
  due_date?: string | null
  // Honored only by the unscoped POST /api/tasks route; the project-scoped
  // route takes the project from its path. Omit to file in General.
  project_id?: number | null
  parent_task_id?: number | null
  estimated_minutes?: number | null
}

export interface TaskUpdate {
  title?: string
  description?: string | null
  workflow_status?: TaskWorkflowStatus
  priority?: TaskPriority
  due_date?: string | null
  // Day-plan snooze; explicit null clears the deferral.
  deferred_until?: string | null
  project_id?: number | null
  parent_task_id?: number | null
  estimated_minutes?: number | null
  repeat_interval?: RepeatInterval | null
  // Recurrence edit-scope control flag consumed by the backend, never a column.
  edit_scope?: EditScope
}
