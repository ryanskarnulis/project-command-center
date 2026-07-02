export type DueStatus = 'overdue' | 'today' | 'soon' | 'none'

const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, medium: 2, low: 3 }

export function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

/** Format a local Date as YYYY-MM-DD. */
export function toISODate(date: Date): string {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/** Today's local date as YYYY-MM-DD. */
export function todayISO(): string {
  return toISODate(new Date())
}

/** Shift a YYYY-MM-DD date by whole days on the local calendar. */
export function addDaysISO(iso: string, days: number): string {
  const date = parseLocalDate(iso)
  date.setDate(date.getDate() + days)
  return toISODate(date)
}

/** 'overdue' if before today, 'today' if today, 'soon' if within `soonDays` (default 3), else 'none'. */
export function dueStatus(due: string | null, soonDays = 3): DueStatus {
  if (!due) return 'none'
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diffDays = Math.round((parseLocalDate(due).getTime() - today.getTime()) / 86_400_000)
  if (diffDays < 0) return 'overdue'
  if (diffDays === 0) return 'today'
  if (diffDays <= soonDays) return 'soon'
  return 'none'
}

export function formatDueDate(due: string | null): string {
  if (!due) return ''
  return parseLocalDate(due).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const RELATIVE_UNITS: [seconds: number, name: string][] = [
  [31_536_000, 'year'],
  [2_592_000, 'month'],
  [604_800, 'week'],
  [86_400, 'day'],
  [3_600, 'hour'],
  [60, 'minute'],
]

/**
 * A coarse "3 days ago" style label for a past ISO timestamp. Returns "just now"
 * under a minute. `now` is injectable for deterministic tests.
 */
export function formatRelative(iso: string, now: number = Date.now()): string {
  const diffSec = Math.floor((now - new Date(iso).getTime()) / 1000)
  if (diffSec < 60) return 'just now'
  for (const [seconds, name] of RELATIVE_UNITS) {
    const value = Math.floor(diffSec / seconds)
    if (value >= 1) return `${value} ${name}${value === 1 ? '' : 's'} ago`
  }
  return 'just now'
}

interface HasDue {
  id: number
  due_date: string | null
}

interface HasDueAndPriority extends HasDue {
  priority: string
}

/**
 * Sort by due date ascending (most overdue / soonest first). Tasks without a
 * due date sort last. Ties (including null-vs-null) break by `id` so order is
 * deterministic.
 */
export function compareByDue(a: HasDue, b: HasDue): number {
  if (a.due_date && b.due_date) {
    if (a.due_date !== b.due_date) return a.due_date < b.due_date ? -1 : 1
  } else if (a.due_date) {
    return -1
  } else if (b.due_date) {
    return 1
  }
  return a.id - b.id
}

/**
 * Sort by due date ascending (nulls last), then priority (urgent→high→medium→low),
 * then id for a fully deterministic order.
 */
export function compareTasks(a: HasDueAndPriority, b: HasDueAndPriority): number {
  if (a.due_date && b.due_date) {
    if (a.due_date !== b.due_date) return a.due_date < b.due_date ? -1 : 1
  } else if (a.due_date) {
    return -1
  } else if (b.due_date) {
    return 1
  }
  const pr = (PRIORITY_RANK[a.priority] ?? 99) - (PRIORITY_RANK[b.priority] ?? 99)
  if (pr !== 0) return pr
  return a.id - b.id
}
