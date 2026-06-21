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
