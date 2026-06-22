import type { DependencyEdge, ProjectGantt } from '../../types/planning'
import type { Task } from '../../types/task'

// The heart of the read-only Gantt slice: a pure mapping from stored task state
// to renderer-ready bars, applying the placement/geometry rules from CURRENT.md.
// No library imports here — this is unit-tested in isolation. When dependency
// auto-shift lands (a later slice that *mutates* dates), that math moves into the
// Python service layer, never here (CLAUDE.md prime directive #1).

const MINUTES_PER_DAY = 480 // 8h * 60

/** Whole calendar days a bar spans, from the estimate. Always at least 1. */
export function spanDays(estimatedMinutes: number | null): number {
  const minutes = estimatedMinutes ?? 0
  return Math.max(1, Math.ceil(minutes / MINUTES_PER_DAY))
}

/** Add `days` to a plain `YYYY-MM-DD` date, timezone-safe (UTC math). */
export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const date = new Date(Date.UTC(y, m - 1, d))
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}

/** A bar ready for the renderer. `end` is the inclusive last day cell. */
export interface GanttBar {
  id: number
  name: string
  start: string
  end: string
  /** `depends_on_task_id`s whose bar must finish first (finish-to-start links). */
  dependsOn: number[]
  /** True when the bar runs past its own `due_date` deadline marker. */
  conflict: boolean
  /** The deadline marker day, shown independent of the bar. */
  dueDate: string | null
  isBlocked: boolean
  isBlocking: boolean
  workflowStatus: Task['workflow_status']
  /** Nesting depth for subtask indentation (0 = top-level). */
  depth: number
  /** The parent task id (for parent-first ordering), or null at top level. */
  parentId: number | null
}

/** A task with neither a start nor a due date — shown in the side bucket. */
export interface UnscheduledTask {
  id: number
  name: string
  isBlocked: boolean
  isBlocking: boolean
}

export interface GanttModel {
  bars: GanttBar[]
  unscheduled: UnscheduledTask[]
}

interface ResolvedSpan {
  start: string
  end: string
  conflict: boolean
}

/**
 * Resolve a task's bar span, or `null` when it belongs in the unscheduled bucket.
 *
 * - `scheduled_start` set → bar starts there, runs `span_days`, and conflicts when
 *   it ends past the `due_date` marker.
 * - else `due_date` set → back-schedule so the bar *finishes on* the deadline (no
 *   conflict by construction).
 * - else → unscheduled.
 */
export function resolveSpan(task: Task): ResolvedSpan | null {
  const span = spanDays(task.estimated_minutes)
  if (task.scheduled_start) {
    const end = addDays(task.scheduled_start, span - 1)
    return {
      start: task.scheduled_start,
      end,
      conflict: task.due_date !== null && end > task.due_date,
    }
  }
  if (task.due_date) {
    return {
      start: addDays(task.due_date, -(span - 1)),
      end: task.due_date,
      conflict: false,
    }
  }
  return null
}

/** `task_id -> [depends_on_task_id, …]`, scoped to the supplied edges. */
function dependsOnMap(dependencies: DependencyEdge[]): Map<number, number[]> {
  const map = new Map<number, number[]>()
  for (const edge of dependencies) {
    const list = map.get(edge.task_id) ?? []
    list.push(edge.depends_on_task_id)
    map.set(edge.task_id, list)
  }
  return map
}

/** Depth of a task in the parent chain, bounded against corrupt cycles. */
function depthOf(task: Task, byId: Map<number, Task>): number {
  let depth = 0
  let current = task.parent_task_id
  const seen = new Set<number>()
  while (current !== null && !seen.has(current)) {
    seen.add(current)
    const parent = byId.get(current)
    if (!parent) break
    depth += 1
    current = parent.parent_task_id
  }
  return depth
}

/**
 * Map the planning payload to renderer-ready bars plus the unscheduled bucket.
 * Bars are ordered so a parent precedes its subtasks (stable within a level by
 * input order), giving a readable nested layout on a flat timeline.
 */
export function buildGanttModel({ tasks, dependencies }: ProjectGantt): GanttModel {
  const byId = new Map(tasks.map((t) => [t.id, t]))
  const deps = dependsOnMap(dependencies)
  const presentIds = new Set(tasks.map((t) => t.id))

  const bars: GanttBar[] = []
  const unscheduled: UnscheduledTask[] = []

  for (const task of tasks) {
    const span = resolveSpan(task)
    if (span === null) {
      unscheduled.push({
        id: task.id,
        name: task.title,
        isBlocked: task.is_blocked,
        isBlocking: task.is_blocking,
      })
      continue
    }
    bars.push({
      id: task.id,
      name: task.title,
      start: span.start,
      end: span.end,
      // Only link to edges whose other endpoint is also drawn as a bar.
      dependsOn: (deps.get(task.id) ?? []).filter(
        (id) => presentIds.has(id) && resolveSpan(byId.get(id)!) !== null,
      ),
      conflict: span.conflict,
      dueDate: task.due_date,
      isBlocked: task.is_blocked,
      isBlocking: task.is_blocking,
      workflowStatus: task.workflow_status,
      depth: depthOf(task, byId),
      parentId: task.parent_task_id,
    })
  }

  return { bars: orderParentFirst(bars), unscheduled }
}

/**
 * Reorder bars so each parent is immediately followed by its descendants (depth
 * already carries the indent). Top-level rows keep their input order; a bar whose
 * parent has no bar (e.g. the parent is done/unscheduled) is treated as top-level.
 */
function orderParentFirst(bars: GanttBar[]): GanttBar[] {
  const childrenOf = new Map<number, GanttBar[]>()
  const present = new Set(bars.map((b) => b.id))
  const roots: GanttBar[] = []
  for (const bar of bars) {
    const parentId = parentBarId(bar, present)
    if (parentId === null) {
      roots.push(bar)
    } else {
      const list = childrenOf.get(parentId) ?? []
      list.push(bar)
      childrenOf.set(parentId, list)
    }
  }
  const ordered: GanttBar[] = []
  const visit = (bar: GanttBar): void => {
    ordered.push(bar)
    for (const child of childrenOf.get(bar.id) ?? []) visit(child)
  }
  roots.forEach(visit)
  return ordered
}

/** A bar's parent id only if that parent is itself drawn; else null (top-level). */
function parentBarId(bar: GanttBar, present: Set<number>): number | null {
  return bar.parentId !== null && present.has(bar.parentId) ? bar.parentId : null
}
