export type DueStatus = 'overdue' | 'today' | 'soon' | 'none'

function parseLocalDate(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
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

interface HasDue {
  id: number
  due_date: string | null
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
