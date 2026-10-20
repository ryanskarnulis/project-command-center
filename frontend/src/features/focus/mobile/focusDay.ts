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
import type {
  BlockedTask,
  CommitmentBlock,
  DueSignal,
  ScheduledBlock,
} from '../../../types/focus'
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

export type DayRowKind = 'done' | 'current' | 'upcoming' | 'commitment'

/** One timeline row: a finished receipt, the band, an upcoming block, or a commitment. */
export interface DayRow {
  kind: DayRowKind
  /** Null only on a commitment row — a fixed block that is not a task. */
  taskId: number | null
  title: string
  /** Minutes the row was planned at. */
  planned: number
  /** Effective minutes: the actual when done, `max(planned, elapsed)` when current. */
  duration: number
  /** Minutes from midnight the row starts at, after any overrun push. */
  start: number
  /** `min-height` in px — the duration-is-height contract. */
  height: number
  /** The plan's block, for meta and row actions. Null on finished and commitment rows. */
  block: ScheduledBlock | null
  /** The commitment this row draws; null on task rows. */
  commitment: CommitmentBlock | null
  /** A fixed row (a commitment or a task pinned at a time) keeps its clock time: nothing pushes it. */
  fixed: boolean
  /** Open minutes between the previous row's end and this row's start (time left before a fixed row). */
  gapBefore: number
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
  /** The fixed row the current block has run into, if any — the notice names it. */
  collision: DayRow | null
  segments: CapacitySegments
}

/** Fixed height of a finished row: the past is a receipt, not a shape. */
export const FINISHED_ROW_HEIGHT = 44
/** The swipe thresholds live with the shared row gesture (#322). */
export { SWIPE_CLAMP_PX, SWIPE_COMMIT_PX } from '../../../hooks/useSwipeGesture'
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

/** A plan block's start as minutes from the plan date's midnight. */
function blockStart(time: string, dayOffset: number): number {
  return dayOffset * 24 * 60 + parseClockTime(time)
}

interface Laid {
  rows: DayRow[]
  currentIndex: number
  dayEnd: number
  doneMinutes: number
  collision: DayRow | null
}

/**
 * Lay the timeline out once. Fixed rows sit at the server's time; flexible rows
 * run end to end from the session start and jump past any fixed row they would
 * collide with. The current block is placed at its planned length and then
 * grown in place to `elapsed` — it is happening now, so it never jumps; an
 * overrun runs into whatever is next, and only flexible rows get pushed.
 */
function layOut(
  finished: FinishedBlock[],
  scheduled: ScheduledBlock[],
  commitments: CommitmentBlock[],
  dayStart: number,
  elapsed: number,
  actual: boolean,
): Laid {
  const rows: DayRow[] = []
  let cursor = dayStart
  let doneMinutes = 0

  for (const block of finished) {
    const duration = actual ? block.minutes : block.plannedMinutes
    rows.push({
      kind: 'done',
      taskId: block.taskId,
      title: block.title,
      planned: block.plannedMinutes,
      duration,
      start: cursor,
      height: FINISHED_ROW_HEIGHT,
      block: null,
      commitment: null,
      fixed: false,
      gapBefore: 0,
    })
    cursor += duration
    doneMinutes += duration
  }

  // Done work leaves the plan, so the first remaining task block is the current one.
  const fixed: DayRow[] = commitments.map((commitment) => ({
    kind: 'commitment',
    taskId: null,
    title: commitment.title,
    planned: commitment.duration_minutes,
    duration: commitment.duration_minutes,
    start: blockStart(commitment.start_time, commitment.start_day_offset),
    height: proportionalHeight(commitment.duration_minutes),
    block: null,
    commitment,
    fixed: true,
    gapBefore: 0,
  }))
  const flexible: DayRow[] = []
  scheduled.forEach((block, index) => {
    const planned = block.estimated_minutes
    const isCurrent = index === 0
    const row: DayRow = {
      kind: isCurrent ? 'current' : 'upcoming',
      taskId: block.task_id,
      title: block.title,
      planned,
      duration: planned,
      start: blockStart(block.start_time, block.start_day_offset),
      height: proportionalHeight(planned),
      block,
      commitment: null,
      fixed: block.fixed,
      gapBefore: 0,
    }
    if (block.fixed) fixed.push(row)
    else flexible.push(row)
  })

  // The current block grows to hold the time actually spent in it; the rest
  // stay at their estimate. A fixed current block grows in place too.
  const grow = (row: DayRow): void => {
    if (row.kind !== 'current' || !actual) return
    row.duration = Math.max(row.planned, Math.round(elapsed))
    row.height = proportionalHeight(row.duration)
  }
  fixed.forEach(grow)

  const collides = (start: number, duration: number): DayRow | undefined =>
    fixed.find((other) => other.start < start + duration && other.start + other.duration > start)

  for (const row of flexible) {
    let start = cursor
    for (let hit = collides(start, row.planned); hit; hit = collides(start, row.planned)) {
      start = hit.start + hit.duration
    }
    row.start = start
    grow(row)
    cursor = start + row.duration
  }

  const timeline = [...rows, ...[...fixed, ...flexible].sort((a, b) => a.start - b.start)]
  let previousEnd = dayStart
  for (const row of timeline) {
    row.gapBefore = Math.max(0, row.start - previousEnd)
    previousEnd = Math.max(previousEnd, row.start + row.duration)
  }
  const currentIndex = timeline.findIndex((row) => row.kind === 'current')
  const current = currentIndex >= 0 ? timeline[currentIndex] : null
  const collision = current
    ? (fixed.find(
        (other) =>
          other !== current &&
          other.start > current.start &&
          other.start < current.start + current.duration,
      ) ?? null)
    : null

  return { rows: timeline, currentIndex, dayEnd: previousEnd, doneMinutes, collision }
}

/**
 * Fold the session's finished blocks, the server's remaining plan and the
 * day's fixed-time commitments into one ordered timeline, then run the
 * design's derivations over it.
 *
 * `elapsed` only affects the current block: it grows past its estimate rather
 * than ending, which pushes the flexible rows after it forward. Fixed rows — a
 * commitment, or a task pinned at a time — never move (#319).
 */
export function buildDayShape({
  finished,
  scheduled,
  commitments = [],
  dayStart,
  capacity,
  elapsed,
}: {
  finished: FinishedBlock[]
  scheduled: ScheduledBlock[]
  commitments?: CommitmentBlock[]
  dayStart: number
  capacity: number
  elapsed: number
}): DayShape {
  const laid = layOut(finished, scheduled, commitments, dayStart, elapsed, true)
  const { rows, currentIndex, dayEnd, doneMinutes, collision } = laid
  const spent = dayEnd - dayStart
  const free = capacity - spent
  const current = currentIndex >= 0 ? rows[currentIndex] : null
  const overage = current ? Math.max(0, Math.round(elapsed) - current.planned) : 0
  // What the day was originally shaped as, so the notice can name the slip.
  const planned = layOut(finished, scheduled, commitments, dayStart, 0, false)
  const shift = Math.round(dayEnd - planned.dayEnd)

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
    collision,
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
