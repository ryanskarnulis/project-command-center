import type { Task } from '../../types/task'
import { dueStatus } from '../../utils/dates'

export type DashboardSignal = 'overdue' | 'blocking' | 'due_today'

export function matchesDashboardSignal(
  task: Task,
  signal: DashboardSignal | null,
): boolean {
  if (signal === null) return true
  if (signal === 'blocking') return task.is_blocking
  const due = dueStatus(task.due_date)
  return signal === 'overdue' ? due === 'overdue' : due === 'today'
}
