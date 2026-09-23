import { isEffectiveTopLevel } from '../features/tasks/taskTree'
import { dueStatus } from './dates'

export type Tone = 'blue' | 'green' | 'orange' | 'red' | 'purple' | 'neutral'

interface HealthTask {
  is_blocked: boolean
  is_blocking: boolean
  due_date: string | null
}

/** What `buildProjectStats` needs to split roots from subtasks (see `isEffectiveTopLevel`). */
interface StatsTask extends HealthTask {
  parent_task_id: number | null
  is_effective_top_level?: boolean
}

/**
 * Derive a project's health label from its open tasks. Shared by project cards,
 * project detail, and dashboard swimlane headers so their tone stays in sync.
 */
export function projectStatus(
  tasks: HealthTask[],
  openCount: number,
): { label: string; tone: Tone } {
  if (openCount === 0) return { label: 'Clear', tone: 'neutral' }
  if (tasks.some((t) => t.is_blocking)) return { label: 'Blocking', tone: 'red' }
  if (tasks.some((t) => t.is_blocked)) return { label: 'Waiting', tone: 'neutral' }
  if (tasks.some((t) => dueStatus(t.due_date) === 'overdue')) {
    return { label: 'At Risk', tone: 'orange' }
  }
  if (tasks.some((t) => dueStatus(t.due_date, 7) !== 'none')) {
    return { label: 'Due Soon', tone: 'blue' }
  }
  return { label: 'On Track', tone: 'green' }
}

export interface ProjectStats {
  /** Open root tasks — what the Tasks tab and the dashboard lane call "open". */
  open: number
  /** Open subtasks, reported beside the roots instead of folded into them. */
  subtasks: number
  done: number
  /**
   * Completed share, 0..1: done / (every open task, subtasks included, + done).
   * The same denominator as the dashboard lane and the Tasks tab, so the three
   * bars can't disagree about one project.
   */
  progress: number
  status: { label: string; tone: Tone }
}

/**
 * Per-project counts + progress + status from its open tasks and done count.
 * Roots and subtasks are split here, not by the caller: the header used to
 * count every row it was handed, so the Overview read "9 open" for a project
 * the Tasks tab read as "4 open · 5 subtasks".
 */
export function buildProjectStats(
  openTasks: StatsTask[],
  doneCount: number,
): ProjectStats {
  const open = openTasks.filter(isEffectiveTopLevel).length
  const total = openTasks.length + doneCount
  return {
    open,
    subtasks: openTasks.length - open,
    done: doneCount,
    progress: total === 0 ? 0 : doneCount / total,
    // Health weighs the whole tree, subtasks included — matching the lanes.
    status: projectStatus(openTasks, openTasks.length),
  }
}
