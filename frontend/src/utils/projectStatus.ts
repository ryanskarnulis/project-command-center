import { dueStatus } from './dates'

export type Tone = 'blue' | 'green' | 'orange' | 'red' | 'purple' | 'neutral'

interface StatusTask {
  is_blocked: boolean
  due_date: string | null
}

/**
 * Derive a project's health label from its open tasks. Mirrors the dashboard's
 * Projects Overview logic so the projects page and the dashboard stay in sync.
 */
export function projectStatus(
  tasks: StatusTask[],
  openCount: number,
): { label: string; tone: Tone } {
  if (openCount === 0) return { label: 'Clear', tone: 'neutral' }
  if (tasks.some((t) => t.is_blocked)) return { label: 'Blocked', tone: 'red' }
  if (tasks.some((t) => dueStatus(t.due_date) === 'overdue')) {
    return { label: 'At Risk', tone: 'orange' }
  }
  if (tasks.some((t) => dueStatus(t.due_date, 7) !== 'none')) {
    return { label: 'Due Soon', tone: 'blue' }
  }
  return { label: 'On Track', tone: 'green' }
}

export interface ProjectStats {
  open: number
  done: number
  /** Completed share, 0..1: done / (open + done). */
  progress: number
  status: { label: string; tone: Tone }
}

/** Per-project counts + progress + status from its open tasks and done count. */
export function buildProjectStats(
  openTasks: StatusTask[],
  doneCount: number,
): ProjectStats {
  const open = openTasks.length
  const total = open + doneCount
  return {
    open,
    done: doneCount,
    progress: total === 0 ? 0 : doneCount / total,
    status: projectStatus(openTasks, open),
  }
}
