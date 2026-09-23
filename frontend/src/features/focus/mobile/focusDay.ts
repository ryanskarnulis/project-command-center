/* Day-shape arithmetic for the M05i mobile /focus timeline.
 *
 * The route's contract is "duration is height": a row's min-height is a linear
 * function of its minutes, gutter hairlines land on real clock boundaries, and
 * the dashed tail is capacity not yet spent. Everything here is pure so the
 * screen can stay a rendering of it.
 *
 * The server plan only carries work that is still open — `get_focus_plan` runs
 * `exclude_done=True` — so a block completed during the session drops out of
 * the next fetch. The finished rows the design keeps at the top of the timeline
 * therefore come from the session log (`useFocusSessionLog`), and are passed in
 * alongside the plan's blocks.
 */
import type { BlockedTask, DueSignal, ScheduledBlock } from '../../../types/focus'
import type { TaskPriority, TaskWorkflowStatus } from '../../../types/task'

/** The clock's three states. `idle` is "not started", not "no block". */
export type RunState = 'idle' | 'running' | 'paused'

/** A block completed during this session, kept client-side (see the note above). */
export interface FinishedBlock {
  taskId: number
  title: string
  /** What the block actually took — the clock's elapsed, or the estimate when it was never started. */
  minutes: number
  /** The estimate it was planned at, so `shift` can tell ran-long from ran-short. */
  plannedMinutes: number
}

export type DayRowKind = 'done' | 'current' | 'upcoming'

/** One timeline row: a finished receipt, the band, or an upcoming block. */
export interface DayRow {
  kind: DayRowKind
  taskId: number
  title: string
  /** Minutes the row was planned at. */
  planned: number
  /** Effective minutes: the actual when done, `max(planned, elapsed)` when current. */
  duration: number
  /** Minutes from midnight the row starts at, after any overrun push. */
  start: number
  /** `min-height` in px — the duration-is-height contract. */
  height: number
  /** The plan's block, for meta and row actions. Null on finished rows. */
  block: ScheduledBlock | null
}

export interface CapacitySegments {
  /** Percentages of the bar's total, in render order. */
  done: number
  planned: number
  over: number
}

export interface DayShape {
  rows: DayRow[]
  /** Index of the current block in `rows`; -1 once the day is clear. */
  currentIndex: number
  /** Session start, minutes from midnight. */
  dayStart: number
  /** Where the last row ends, minutes from midnight. */
  dayEnd: number
  capacity: number
  /** `dayEnd - dayStart`: everything the day now costs, finished work included. */
  spent: number
  /** `capacity - spent`; negative means over capacity. */
  free: number
  /** Minutes the current block has run past its estimate. */
  overage: number
  /** How far the day slipped from its original shape; > 0 shows the amber notice. */
  shift: number
  segments: CapacitySegments
}

/** Fixed height of a finished row: the past is a receipt, not a shape. */
export const FINISHED_ROW_HEIGHT = 44
/** Past this the gesture commits; below it the row snaps back. */
export const SWIPE_COMMIT_PX = 88
/** The drag cannot pull the content further than this. */
export const SWIPE_CLAMP_PX = 132
/** How long the undo bar stands after a swipe. */
export const UNDO_LIFETIME_MS = 5000

/**
 * Wall-clock duration label: 165 → "2h 45m", 120 → "2h", 45 → "45m".
 *
 * Deliberately not `formatDurationShort`, which splits on the largest whole
 * unit and so renders 165 as "165m". A day plan reads in hours and minutes
 * together, and only ever spans one day.
 */
export function formatClockDuration(minutes: number): string {
  const total = Math.round(minutes)
  if (total <= 0) return '0m'
  const hours = Math.floor(total / 60)
  const rest = total % 60
  if (!hours) return `${rest}m`
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

/** Minutes from midnight → "HH:MM", wrapping a session that runs past midnight. */
export function formatClockTime(minutes: number): string {
  const total = Math.round(minutes)
  const hours = Math.floor(total / 60) % 24
  const rest = ((total % 60) + 60) % 60
  return `${String(hours).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
}

/** "HH:MM" → minutes from midnight. */
export function parseClockTime(value: string): number {
  const [hours, minutes] = value.split(':')
  return Number(hours) * 60 + Number(minutes)
}

/** Minutes from midnight for a session start, guarding a malformed stored value. */
export function sessionStartMinutes(startTime: string): number {
  const parsed = parseClockTime(startTime)
  return Number.isFinite(parsed) ? parsed : 0
}

/** Row min-height in px. Linear in duration, capped so a 4h block can't own the screen. */
export function proportionalHeight(minutes: number): number {
  return Math.round(Math.min(184, 48 + Math.max(0, minutes) * 0.63))
}

/** The free tail's min-height: the same slope, floored so an almost-full day still reads. */
export function tailHeight(freeMinutes: number): number {
  return Math.max(76, Math.min(140, Math.round(48 + Math.max(0, freeMinutes) * 0.63)))
}

/**
 * Fold the session's finished blocks and the server's remaining plan into one
 * ordered timeline, then run the design's derivations over it.
 *
 * `elapsed` only affects the current block: it grows past its estimate rather
 * than ending, which is what pushes every later row forward.
 */
export function buildDayShape({
  finished,
  scheduled,
  dayStart,
  capacity,
  elapsed,
}: {
  finished: FinishedBlock[]
  scheduled: ScheduledBlock[]
  dayStart: number
  capacity: number
  elapsed: number
}): DayShape {
  const currentIndex = scheduled.length ? finished.length : -1
  const rows: DayRow[] = []
  let cursor = dayStart
  let doneMinutes = 0

  for (const block of finished) {
    rows.push({
      kind: 'done',
      taskId: block.taskId,
      title: block.title,
      planned: block.plannedMinutes,
      duration: block.minutes,
      start: cursor,
      height: FINISHED_ROW_HEIGHT,
      block: null,
    })
    cursor += block.minutes
    doneMinutes += block.minutes
  }

  scheduled.forEach((block, offset) => {
    const index = finished.length + offset
    const planned = block.estimated_minutes
    // The current block grows to hold the time actually spent in it; the rest
    // stay at their estimate.
    const duration = index === currentIndex ? Math.max(planned, Math.round(elapsed)) : planned
    rows.push({
      kind: index === currentIndex ? 'current' : 'upcoming',
      taskId: block.task_id,
      title: block.title,
      planned,
      duration,
      start: cursor,
      height: proportionalHeight(duration),
      block,
    })
    cursor += duration
  })

  const dayEnd = cursor
  const spent = dayEnd - dayStart
  const free = capacity - spent
  const current = currentIndex >= 0 ? rows[currentIndex] : null
  const overage = current ? Math.max(0, Math.round(elapsed) - current.planned) : 0
  // What the day was originally shaped as, so the notice can name the slip.
  const plannedEnd = rows.reduce((total, row) => total + row.planned, dayStart)
  const shift = Math.round(dayEnd - plannedEnd)

  const barTotal = Math.max(1, Math.max(capacity, spent))
  const doneSegment = Math.min(doneMinutes, capacity)
  const segments: CapacitySegments = {
    done: (doneSegment / barTotal) * 100,
    planned: (Math.max(0, Math.min(spent, capacity) - doneSegment) / barTotal) * 100,
    over: (Math.max(0, spent - capacity) / barTotal) * 100,
  }

  return {
    rows,
    currentIndex,
    dayStart,
    dayEnd,
    capacity,
    spent,
    free,
    overage,
    shift,
    segments,
  }
}

/* --- row meta -------------------------------------------------------------
   Hard cap of two items: one coloured signal, then one context string. A third
   (the second flag) is dropped — it lives on the task detail and in the ⋯
   sheet. Three chips wrapped to two lines at 390px, which is what the cap is
   for. */

/** The signal word a row leads with, and the accent it wears. */
export interface RowSignal {
  label: string
  /** A CSS custom-property name from the accent set, not a literal colour. */
  tone: 'danger' | 'warning' | 'workflow' | 'live'
}

/**
 * The loudest true fact about a row, or null. Ranked: a missed date beats an
 * urgency, an urgency beats a state you are already in, and a state beats a
 * date that has not passed yet.
 */
export function rowSignal(
  priority: TaskPriority,
  workflowStatus: TaskWorkflowStatus,
  dueSignal: DueSignal,
): RowSignal | null {
  if (dueSignal === 'overdue') return { label: 'Overdue', tone: 'danger' }
  if (priority === 'urgent') return { label: 'Urgent', tone: 'danger' }
  if (workflowStatus === 'in_progress') return { label: 'In progress', tone: 'workflow' }
  if (priority === 'high') return { label: 'High', tone: 'warning' }
  if (dueSignal === 'due_today') return { label: 'Due today', tone: 'live' }
  if (dueSignal === 'due_soon') return { label: 'Due soon', tone: 'warning' }
  return null
}

/**
 * The row's context string — the second and last meta item. One optional
 * context part, then the duration; a recurrence only earns the slot when
 * nothing else claimed it.
 */
export function rowContext(block: ScheduledBlock, blocksCount: number): string {
  const duration = block.estimate_assumed
    ? `${formatClockDuration(block.estimated_minutes)} est.`
    : formatClockDuration(block.estimated_minutes)
  if (block.parent_title) return `Part of ${block.parent_title} · ${duration}`
  if (blocksCount > 0) return `Blocks ${blocksCount} · ${duration}`
  if (block.is_recurring) return `${duration} · Repeats`
  return duration
}

/**
 * How many blocked tasks each scheduled task is holding up, so a row can say
 * "Blocks 2". Derived from the plan's own blocked list — no extra request.
 */
export function blockingCounts(blocked: BlockedTask[]): Map<number, number> {
  const counts = new Map<number, number>()
  for (const task of blocked) {
    for (const blocker of task.blocking_tasks) {
      counts.set(blocker.task_id, (counts.get(blocker.task_id) ?? 0) + 1)
    }
  }
  return counts
}
