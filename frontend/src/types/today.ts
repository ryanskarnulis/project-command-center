import type { TaskPriority, TaskWorkflowStatus } from './task'

// Mirrors backend app/schemas/today.py. How a task's due date relates to the
// plan's target day; derived in the scheduler, no stored column.
export type DueSignal = 'overdue' | 'due_today' | 'due_soon' | 'none'

export interface ScheduledBlock {
  task_id: number
  title: string
  project_id: number | null
  start_time: string // HH:MM
  end_time: string // HH:MM
  estimated_minutes: number
  // True when the duration was assumed (task had no estimate). The UI marks
  // these so it doesn't pretend the task is sized.
  estimate_assumed: boolean
  priority: TaskPriority
  workflow_status: TaskWorkflowStatus
  due_date: string | null
  due_signal: DueSignal
  // True when the underlying task recurs, so the row can offer "skip occurrence".
  is_recurring: boolean
  // Deterministic, human-readable placement explanation. No model prose.
  reason: string
  // Set when this block is a subtask standing in for a parent that didn't fit
  // the remaining capacity; the UI labels it "part of <parent_title>".
  parent_task_id: number | null
  parent_title: string | null
}

export interface OverflowTask {
  task_id: number
  title: string
  project_id: number | null
  priority: TaskPriority
  workflow_status: TaskWorkflowStatus
  due_date: string | null
  due_signal: DueSignal
  // True when the underlying task recurs, so the row can offer "skip occurrence".
  is_recurring: boolean
  estimated_minutes: number
  estimate_assumed: boolean
  // How many of this task's subtasks made the timeline in its place.
  scheduled_subtask_count: number
}

export interface BlockingTask {
  task_id: number
  title: string
  workflow_status: TaskWorkflowStatus
}

export interface BlockedTask {
  task_id: number
  title: string
  project_id: number | null
  priority: TaskPriority
  due_date: string | null
  // Active dependencies that are not yet done — what the UI warns about. Each
  // carries title + workflow status so a blocked row is self-explanatory.
  blocking_tasks: BlockingTask[]
}

export interface TodayPlan {
  date: string // YYYY-MM-DD
  start_time: string // HH:MM
  available_minutes: number
  used_minutes: number
  scheduled: ScheduledBlock[]
  overflow: OverflowTask[]
  blocked: BlockedTask[]
}
