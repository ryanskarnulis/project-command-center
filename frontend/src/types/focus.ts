import type { TaskPriority, TaskWorkflowStatus } from './task'

// Mirrors backend app/schemas/focus.py. How a task's due date relates to the
// plan's target day; derived in the scheduler, no stored column.
export type DueSignal = 'overdue' | 'due_today' | 'due_soon' | 'none'

export interface ScheduledBlock {
  task_id: number
  title: string
  project_id: number | null
  start_time: string // HH:MM
  end_time: string // HH:MM
  // Whole local calendar days after FocusPlan.date.
  start_day_offset: number
  end_day_offset: number
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
  // The stored day plan (#319): the pin this block came from, whether the task
  // is pinned into the day, and whether that pin holds a clock time (a fixed
  // block never moves).
  entry_id: number | null
  pinned: boolean
  fixed: boolean
  // A floating pin's place among the day's floating pins, 0 first (#375). The
  // order a reorder edits: first-fit packing can put a later pin earlier on the
  // clock. Null for every other block.
  pin_order: number | null
}

/** A fixed-time commitment that is not a task (a meeting). */
export interface CommitmentBlock {
  entry_id: number
  title: string
  start_time: string // HH:MM
  end_time: string // HH:MM
  start_day_offset: number
  end_day_offset: number
  duration_minutes: number
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

export interface FocusPlan {
  date: string // YYYY-MM-DD
  start_time: string // HH:MM
  available_minutes: number
  used_minutes: number
  scheduled: ScheduledBlock[]
  // Fixed-time commitments in start order; kept apart so every scheduled block
  // is a task.
  commitments: CommitmentBlock[]
  overflow: OverflowTask[]
  blocked: BlockedTask[]
}

/** One stored decision about a day's plan, as the write routes return it. */
export interface FocusEntry {
  id: number
  plan_date: string // YYYY-MM-DD
  task_id: number | null
  title: string | null
  start_time: string | null // HH:MM; null = a floating pin
  duration_minutes: number | null
  position: number
  created_at: string
  deleted_at: string | null
}

export interface FocusEntryUpdate {
  start_time?: string | null
  title?: string
  duration_minutes?: number
}
