import type { Task, TaskWorkflowStatus } from '../../types/task'

/**
 * Blocking gates completion only: a move into Done requires every dependency
 * finished. "Blocked" is derived server-side (is_blocked); boards mirror the
 * same rule the backend enforces rather than letting an illegal transition reach
 * the API. A blocked task may still be started (In progress) or returned to Open.
 */
export function isMoveBlocked(task: Task, target: TaskWorkflowStatus): boolean {
  return task.is_blocked && task.workflow_status !== 'done' && target === 'done'
}
