import type { Task, TaskWorkflowStatus } from '../../types/task'

/** Blocked tasks may return to Open, but cannot advance until dependencies finish. */
export function isMoveBlocked(task: Task, target: TaskWorkflowStatus): boolean {
  return task.is_blocked && task.workflow_status !== 'done' && target !== 'open'
}
