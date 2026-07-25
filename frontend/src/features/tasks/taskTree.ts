import type { Task } from '../../types/task'

export interface TaskTree {
  roots: Task[]
  childrenOf: Map<number, Task[]>
}

/**
 * Group a flat task list into a parent/child tree. A task is a child only when
 * its `parent_task_id` points at a task that is also present in `tasks`; an
 * orphaned child (parent filtered out or missing) is promoted to a root so it
 * never disappears from view.
 */
/**
 * Whether a task behaves as a root on a flat surface (Kanban board, dashboard
 * lanes, completed archives).
 *
 * Mirrors the backend `tasks.is_effective_top_level`: a task with no parent, or
 * whose parent is trashed/purged, is top-level. The server resolves that against
 * *all* active tasks and ships the answer as `is_effective_top_level`; we can't
 * recompute it client-side, because a page's task list is filtered (open-only,
 * project-scoped) and a merely-absent parent would falsely promote a real
 * subtask. Falls back to raw nullness only for a task that never came from the
 * API (test fixtures, optimistic local rows).
 */
export function isEffectiveTopLevel(task: Task): boolean {
  return task.is_effective_top_level ?? task.parent_task_id === null
}

export function buildTaskTree(tasks: Task[]): TaskTree {
  const ids = new Set(tasks.map((t) => t.id))
  const childrenOf = new Map<number, Task[]>()
  const roots: Task[] = []
  for (const t of tasks) {
    if (t.parent_task_id !== null && ids.has(t.parent_task_id)) {
      const group = childrenOf.get(t.parent_task_id) ?? []
      group.push(t)
      childrenOf.set(t.parent_task_id, group)
    } else {
      roots.push(t)
    }
  }
  return { roots, childrenOf }
}
