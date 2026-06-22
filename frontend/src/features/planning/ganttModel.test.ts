import { describe, expect, it } from 'vitest'
import type { Task } from '../../types/task'
import { addDays, buildGanttModel, resolveSpan, spanDays } from './ganttModel'

function task(overrides: Partial<Task> & { id: number }): Task {
  return {
    project_id: 1,
    inbox_item_id: null,
    parent_task_id: null,
    title: `Task ${overrides.id}`,
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'medium',
    due_date: null,
    scheduled_start: null,
    estimated_minutes: null,
    repeat_interval: null,
    recurrence_id: null,
    confidence: null,
    assignee_hint: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    is_blocked: false,
    is_blocking: false,
    blocked_task_count: 0,
    has_subtasks: false,
    ...overrides,
  }
}

describe('spanDays', () => {
  it('rounds up to whole 8h days, with a floor of 1', () => {
    expect(spanDays(null)).toBe(1)
    expect(spanDays(0)).toBe(1)
    expect(spanDays(30)).toBe(1) // 30m -> 1d
    expect(spanDays(480)).toBe(1) // exactly 8h -> 1d
    expect(spanDays(481)).toBe(2)
    expect(spanDays(600)).toBe(2) // 10h -> 2d
    expect(spanDays(960)).toBe(2) // 16h -> 2d
  })
})

describe('addDays', () => {
  it('is timezone-safe across a month boundary', () => {
    expect(addDays('2026-06-30', 1)).toBe('2026-07-01')
    expect(addDays('2026-06-01', -1)).toBe('2026-05-31')
    expect(addDays('2026-06-15', 0)).toBe('2026-06-15')
  })
})

describe('resolveSpan', () => {
  it('starts at scheduled_start and runs span_days (inclusive end)', () => {
    const span = resolveSpan(
      task({ id: 1, scheduled_start: '2026-06-20', estimated_minutes: 600 }),
    )
    expect(span).toEqual({ start: '2026-06-20', end: '2026-06-21', conflict: false })
  })

  it('flags a conflict when the bar ends past the due_date', () => {
    const span = resolveSpan(
      task({
        id: 1,
        scheduled_start: '2026-06-20',
        estimated_minutes: 1440, // 3 days -> ends 06-22
        due_date: '2026-06-21',
      }),
    )
    expect(span).toEqual({ start: '2026-06-20', end: '2026-06-22', conflict: true })
  })

  it('back-schedules a due-only task to finish on the deadline', () => {
    const span = resolveSpan(
      task({ id: 1, due_date: '2026-06-25', estimated_minutes: 600 }), // 2 days
    )
    expect(span).toEqual({ start: '2026-06-24', end: '2026-06-25', conflict: false })
  })

  it('returns null for a task with neither start nor due date', () => {
    expect(resolveSpan(task({ id: 1 }))).toBeNull()
  })
})

describe('buildGanttModel', () => {
  it('splits scheduled bars from the unscheduled bucket', () => {
    const model = buildGanttModel({
      tasks: [
        task({ id: 1, scheduled_start: '2026-06-20' }),
        task({ id: 2, due_date: '2026-06-22' }),
        task({ id: 3 }), // unscheduled
      ],
      dependencies: [],
    })
    expect(model.bars.map((b) => b.id)).toEqual([1, 2])
    expect(model.unscheduled.map((u) => u.id)).toEqual([3])
  })

  it('maps a dependency edge to a depends-on link', () => {
    const model = buildGanttModel({
      tasks: [
        task({ id: 1, scheduled_start: '2026-06-20' }),
        task({ id: 2, scheduled_start: '2026-06-22' }),
      ],
      dependencies: [{ task_id: 2, depends_on_task_id: 1 }],
    })
    expect(model.bars.find((b) => b.id === 2)?.dependsOn).toEqual([1])
  })

  it('drops a link whose other endpoint has no bar (unscheduled/absent)', () => {
    const model = buildGanttModel({
      tasks: [
        task({ id: 1 }), // unscheduled -> no bar
        task({ id: 2, scheduled_start: '2026-06-22' }),
      ],
      dependencies: [
        { task_id: 2, depends_on_task_id: 1 },
        { task_id: 2, depends_on_task_id: 99 }, // absent
      ],
    })
    expect(model.bars.find((b) => b.id === 2)?.dependsOn).toEqual([])
  })

  it('carries has_subtasks and estimated_minutes onto the bar (for resize)', () => {
    const model = buildGanttModel({
      tasks: [
        task({
          id: 1,
          scheduled_start: '2026-06-20',
          estimated_minutes: 600,
          has_subtasks: true,
        }),
      ],
      dependencies: [],
    })
    const bar = model.bars.find((b) => b.id === 1)
    expect(bar?.hasSubtasks).toBe(true)
    expect(bar?.estimatedMinutes).toBe(600)
  })

  it('carries project_id onto bars and unscheduled items (global grouping)', () => {
    const model = buildGanttModel({
      tasks: [
        task({ id: 1, project_id: 7, scheduled_start: '2026-06-20' }),
        task({ id: 2, project_id: 9 }), // unscheduled
      ],
      dependencies: [],
    })
    expect(model.bars.find((b) => b.id === 1)?.projectId).toBe(7)
    expect(model.unscheduled.find((u) => u.id === 2)?.projectId).toBe(9)
  })

  it('orders subtasks immediately after their parent with a depth', () => {
    const model = buildGanttModel({
      tasks: [
        task({ id: 1, scheduled_start: '2026-06-20' }),
        task({ id: 2, scheduled_start: '2026-06-21' }),
        task({ id: 3, parent_task_id: 1, scheduled_start: '2026-06-22' }),
      ],
      dependencies: [],
    })
    expect(model.bars.map((b) => b.id)).toEqual([1, 3, 2])
    expect(model.bars.find((b) => b.id === 3)?.depth).toBe(1)
    expect(model.bars.find((b) => b.id === 1)?.depth).toBe(0)
  })
})
