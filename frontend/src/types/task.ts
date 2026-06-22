export type TaskReviewStatus = 'candidate' | 'accepted' | 'rejected'
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
  inbox_item_id: number | null
  parent_task_id: number | null
  title: string
  description: string | null
  review_status: TaskReviewStatus
  workflow_status: TaskWorkflowStatus
  priority: TaskPriority
  due_date: string | null
  // Planning/Gantt slice: the day a task's bar starts (YYYY-MM-DD), or null when
  // not explicitly placed. Bar length is derived from `estimated_minutes`.
  scheduled_start: string | null
  estimated_minutes: number | null
  repeat_interval: RepeatInterval | null
  recurrence_id: string | null
  confidence: number | null
  assignee_hint: string | null
  created_at: string
  updated_at: string
  deleted_at?: string | null
  // Derived server-side: true while any dependency is unfinished.
  is_blocked: boolean
  // Derived server-side: true when this is the top active blocker for downstream work.
  is_blocking: boolean
  // Derived server-side: transitive count of unfinished accepted tasks waiting on it.
  blocked_task_count: number
  // Derived server-side: true when the task has accepted subtasks. When true,
  // `estimated_minutes` and `workflow_status` carry rolled-up values and are
  // read-only (set them by editing the subtasks instead).
  has_subtasks: boolean
}

/** A recurrence series: every occurrence sharing a `recurrence_id`. */
export interface TaskSeries {
  recurrence_id: string
  // Oldest due date first; includes soft-deleted (skipped) occurrences, which
  // carry a non-null `deleted_at`.
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

/** Per-subtask edits applied when approving a suggested breakdown subtask. */
export interface SubtaskEdit {
  title?: string
  description?: string | null
  priority?: TaskPriority
  estimated_minutes?: number | null
}

export interface SubtaskDecision {
  task_id: number
  action: 'approve' | 'dismiss'
  edits?: SubtaskEdit
}

export interface BreakdownReviewResult {
  approved: number
  dismissed: number
  finalized: boolean
  training_example_id: number | null
}

export interface TaskCreate {
  title: string
  description?: string | null
  review_status?: TaskReviewStatus
  workflow_status?: TaskWorkflowStatus
  priority?: TaskPriority
  due_date?: string | null
  parent_task_id?: number | null
  estimated_minutes?: number | null
  assignee_hint?: string | null
}

export interface TaskUpdate {
  title?: string
  description?: string | null
  review_status?: TaskReviewStatus
  workflow_status?: TaskWorkflowStatus
  priority?: TaskPriority
  due_date?: string | null
  // Planning/Gantt slice: accepted by the API but not yet written by the UI (the
  // drag-to-reschedule slice wires it). Omitted = untouched; null clears it.
  scheduled_start?: string | null
  project_id?: number | null
  parent_task_id?: number | null
  estimated_minutes?: number | null
  assignee_hint?: string | null
  repeat_interval?: RepeatInterval | null
  // Recurrence edit-scope control flag consumed by the backend, never a column.
  edit_scope?: EditScope
}
