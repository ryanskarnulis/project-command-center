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
}

export interface TaskDependency {
  id: number
  task_id: number
  depends_on_task_id: number
  depends_on_title: string
  depends_on_workflow_status: TaskWorkflowStatus
  depends_on_done: boolean
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
  project_id?: number | null
  parent_task_id?: number | null
  estimated_minutes?: number | null
  assignee_hint?: string | null
  repeat_interval?: RepeatInterval | null
  // Recurrence edit-scope control flag consumed by the backend, never a column.
  edit_scope?: EditScope
}
