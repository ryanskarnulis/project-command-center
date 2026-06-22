import { describe, expect, it } from 'vitest'
import type { GanttBar } from './ganttModel'
import { computeViolations, violatingDependentIds } from './dependencyConflicts'

function bar(overrides: Partial<GanttBar> & { id: number }): GanttBar {
  return {
    name: `Task ${overrides.id}`,
    projectId: 1,
    start: '2026-06-20',
    end: '2026-06-20',
    dependsOn: [],
    conflict: false,
    dueDate: null,
    isBlocked: false,
    isBlocking: false,
    workflowStatus: 'open',
    depth: 0,
    parentId: null,
    hasSubtasks: false,
    estimatedMinutes: null,
    ...overrides,
  }
}

describe('computeViolations', () => {
  it('flags a dependent that starts on or before its blocker ends', () => {
    const blocker = bar({ id: 1, start: '2026-06-20', end: '2026-06-22' })
    // Dependent starts the day before the blocker finishes -> violation.
    const dependent = bar({
      id: 2,
      start: '2026-06-21',
      end: '2026-06-21',
      dependsOn: [1],
    })
    const violations = computeViolations([blocker, dependent])
    expect(violations).toEqual([
      { dependentId: 2, blockerId: 1, suggestedStart: '2026-06-23' },
    ])
  })

  it('flags a dependent that starts exactly on the blocker end day', () => {
    const blocker = bar({ id: 1, start: '2026-06-20', end: '2026-06-22' })
    const dependent = bar({ id: 2, start: '2026-06-22', dependsOn: [1] })
    const violations = computeViolations([blocker, dependent])
    expect(violations).toHaveLength(1)
    expect(violations[0].suggestedStart).toBe('2026-06-23')
  })

  it('does not flag a dependent that starts the day after the blocker ends', () => {
    const blocker = bar({ id: 1, start: '2026-06-20', end: '2026-06-22' })
    const dependent = bar({ id: 2, start: '2026-06-23', dependsOn: [1] })
    expect(computeViolations([blocker, dependent])).toEqual([])
  })

  it('ignores a blocker that is not drawn (no bar in the set)', () => {
    // `dependsOn` is normally pre-filtered to drawn bars; guard regardless.
    const dependent = bar({ id: 2, start: '2026-06-21', dependsOn: [99] })
    expect(computeViolations([dependent])).toEqual([])
  })
})

describe('violatingDependentIds', () => {
  it('collects the dependent ids that violate', () => {
    const violations = [
      { dependentId: 2, blockerId: 1, suggestedStart: '2026-06-23' },
      { dependentId: 2, blockerId: 3, suggestedStart: '2026-06-25' },
      { dependentId: 4, blockerId: 1, suggestedStart: '2026-06-23' },
    ]
    expect(violatingDependentIds(violations)).toEqual(new Set([2, 4]))
  })
})
