import type { Task, TaskWorkflowStatus } from '../../types/task'

/**
 * A move into In progress or Done requires every dependency finished. "Blocked"
 * is derived server-side (is_blocked); boards mirror the same rule the list and
 * Focus views enforce rather than letting an illegal transition reach the API.
 * Blocked tasks may still return to Open.
 */
export function isMoveBlocked(task: Task, target: TaskWorkflowStatus): boolean {
  return task.is_blocked && task.workflow_status !== 'done' && target !== 'open'
}
