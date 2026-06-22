import { addDays, type GanttBar } from './ganttModel'

// Pure dependency-conflict detection for the planning Gantt (Slice 4). No React,
// no DOM — unit-tested in isolation like `ganttModel`. A *dependency violation* is
// distinct from a bar's own `conflict` flag: `conflict` means the bar runs past its
// own `due_date` deadline; a violation here means a dependent is scheduled to start
// on or before its blocker finishes (finish-to-start: it should start no earlier
// than `blocker.end + 1`). Keep the two notions separate.
//
// The autofix suggestion is a single-task nudge (`blocker.end + 1`). Cascading the
// shift through the dependency graph is a later slice (pure Python in
// `services/planning.py`), not frontend date math (CLAUDE.md prime directive #1).

/** A dependent scheduled to start on or before its blocker finishes. */
export interface DependencyViolation {
  /** The task that starts too early (the edge's `task_id`). */
  dependentId: number
  /** The blocker it depends on (the edge's `depends_on_task_id`). */
  blockerId: number
  /** Where a one-task autofix would move the dependent: `blocker.end + 1` day. */
  suggestedStart: string
}

/**
 * Every finish-to-start violation among the drawn bars. For each bar, walk its
 * `dependsOn` blockers (already filtered to bars that are themselves drawn, so the
 * lookup always resolves) and emit a violation when the dependent starts on or
 * before the blocker's inclusive last day.
 */
export function computeViolations(bars: GanttBar[]): DependencyViolation[] {
  const byId = new Map(bars.map((b) => [b.id, b]))
  const violations: DependencyViolation[] = []
  for (const bar of bars) {
    for (const blockerId of bar.dependsOn) {
      const blocker = byId.get(blockerId)
      if (!blocker) continue
      if (bar.start <= blocker.end) {
        violations.push({
          dependentId: bar.id,
          blockerId,
          suggestedStart: addDays(blocker.end, 1),
        })
      }
    }
  }
  return violations
}

/** Ids of the dependent bars that violate, for the renderer's warning styling. */
export function violatingDependentIds(
  violations: DependencyViolation[],
): Set<number> {
  return new Set(violations.map((v) => v.dependentId))
}
