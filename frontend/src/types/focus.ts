import type { RepeatInterval, TaskPriority, TaskWorkflowStatus } from './task'

/**
 * A commitment series' cadence (#376): the task shape plus, for `unit: 'week'`,
 * an optional weekday set (#427) — Monday 0 to Sunday 6, sorted. Mirrors the
 * backend CommitmentRepeatInterval; task recurrence never carries `days`.
 */
export interface CommitmentRepeatInterval extends RepeatInterval {
  days?: number[]
}

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
  // Deterministic, human-readable placement explanation. No model prose. For
  // the agent and API clients: the desktop row doesn't render it, since every
  // token is already on the row from the fields above (#498).
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
  // Set when the commitment repeats (#376). `entry_id` is then the series:
  // editing or removing it changes every day; skipping takes out one day.
  repeat_interval: CommitmentRepeatInterval | null
  // A series' first day and its end (#428): it occurs from `series_start_date`
  // up to the day before `stop_date` (null = no end). Both null on a one-off.
  // `stopFocusSeries` moves the end, and only after the first day.
  series_start_date: string | null
  stop_date: string | null
  // Set when this day's occurrence was edited for that day only (#428): the
  // block shows the edit, `entry_id` is still the series, and
  // `removeFocusEntry(override_entry_id)` resets the day.
  override_entry_id: number | null
}

/**
 * A repeating commitment's occurrence skipped for the day (#429). Holds no
 * time; listed so the skip can be taken back. `entry_id` is the series,
 * `skip_entry_id` the skip: `removeFocusEntry(skip_entry_id)` un-skips it.
 */
export interface SkippedCommitmentBlock extends CommitmentBlock {
  skip_entry_id: number
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
  // Occurrences skipped for this day (#429), in start order. Not part of the
  // day's time; shown muted with an Un-skip.
  skipped_commitments: SkippedCommitmentBlock[]
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
  // A repeating commitment's cadence (#376); null on every other kind.
  repeat_interval: CommitmentRepeatInterval | null
  // A series' end (#428): the first day it no longer occurs; null = no end.
  stop_date: string | null
  // Set only on a skip or a one-day edit: the series whose `plan_date`
  // occurrence it skips or edits.
  series_id: number | null
  created_at: string
  deleted_at: string | null
}

/** A one-day edit of a repeating commitment (#428); fields left out follow the series. */
export interface FocusOccurrenceEdit {
  start_time?: string
  title?: string
  duration_minutes?: number
}

export interface FocusEntryUpdate {
  start_time?: string | null
  title?: string
  duration_minutes?: number
}

// --- The session log and running clock (#374) ------------------------------

export type FocusClockState = 'running' | 'paused'

/** The one live clock. `elapsed_ms` is as of the response, measured server-side. */
export interface FocusClock {
  id: number
  plan_date: string // YYYY-MM-DD
  task_id: number
  state: FocusClockState
  elapsed_ms: number
  leg_started_at: string | null
}

/** A block booked into a day's session log. */
export interface FocusFinishedBlock {
  id: number
  plan_date: string // YYYY-MM-DD
  task_id: number
  title: string
  minutes: number
  planned_minutes: number
  finished_at: string
}

export interface FocusSessionLog {
  plan_date: string
  finished: FocusFinishedBlock[]
}
