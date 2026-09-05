import type { Task, TaskWorkflowStatus } from '../../types/task'
import { WORKFLOW_STATUSES } from './taskMeta'

/**
 * Blocking gates completion only: a move into Done requires every dependency
 * finished. "Blocked" is derived server-side (is_blocked); boards mirror the
 * same rule the backend enforces rather than letting an illegal transition reach
 * the API. A blocked task may still be started (In progress) or returned to Open.
 */
export function isMoveBlocked(task: Task, target: TaskWorkflowStatus): boolean {
  return task.is_blocked && task.workflow_status !== 'done' && target === 'done'
}

/**
 * Why a status move on a task with subtasks is refused, or null when allowed.
 *
 * Mirrors the server's guard (services/tasks._parent_status_write_takes_effect)
 * so an impossible move is refused up front instead of round-tripping to a 409.
 * A parent's Done is derived — only its subtasks complete it. Open and In
 * progress are the parent's own to set, but only while its subtasks leave the
 * question open (`subtask_status === 'open'`): once any subtask has moved they
 * pin the parent at In progress, and once all are done they pin it at Done. A
 * task whose `subtask_status` is unknown (locally constructed) is left to the
 * server.
 */
export function subtaskMoveRefusal(
  task: Task,
  target: TaskWorkflowStatus,
): string | null {
  if (!task.has_subtasks) return null
  if (target === 'done') return 'Complete its subtasks to complete it'
  const pinned = task.subtask_status
  if (pinned === 'done') return 'Reopen a subtask to reopen it'
  if (pinned === 'in_progress' && target === 'open') {
    return 'Its subtasks are under way; reopen them first'
  }
  return null
}

/**
 * Why no status move at all is possible on this task, or null. Only a parent
 * whose subtasks are all done qualifies: every target is either refused or
 * already what it reads. Boards use it to switch off dragging; chips to go
 * read-only.
 */
export function statusLockedReason(task: Task): string | null {
  return task.has_subtasks && task.subtask_status === 'done'
    ? 'Reopen a subtask to reopen it'
    : null
}

/** Per-option hints for a status menu: the targets `subtaskMoveRefusal` refuses. */
export function refusedStatusOptions(
  task: Task,
): Partial<Record<TaskWorkflowStatus, string>> | undefined {
  const refused: Partial<Record<TaskWorkflowStatus, string>> = {}
  for (const status of WORKFLOW_STATUSES) {
    const reason = subtaskMoveRefusal(task, status)
    if (reason) refused[status] = reason
  }
  return Object.keys(refused).length > 0 ? refused : undefined
}
