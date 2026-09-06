import { describe, expect, it } from 'vitest'
import { buildProjectStats, projectStatus } from './projectStatus'

function daysFromNow(n: number): string {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + n)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

describe('projectStatus', () => {
  it('is Clear when there are no open tasks', () => {
    expect(projectStatus([], 0)).toEqual({ label: 'Clear', tone: 'neutral' })
  })

  it('is Blocking when any open task is a top-level blocker', () => {
    expect(
      projectStatus([{ is_blocked: false, is_blocking: true, due_date: null }], 1),
    ).toEqual({ label: 'Blocking', tone: 'red' })
  })

  it('is Waiting when tasks are blocked but none are top-level blockers', () => {
    expect(
      projectStatus([{ is_blocked: true, is_blocking: false, due_date: null }], 1),
    ).toEqual({ label: 'Waiting', tone: 'neutral' })
  })

  it('is At Risk when any task is overdue', () => {
    expect(
      projectStatus(
        [{ is_blocked: false, is_blocking: false, due_date: daysFromNow(-2) }],
        1,
      ).label,
    ).toBe('At Risk')
  })

  it('is Due Soon when a task is due within a week', () => {
    expect(
      projectStatus(
        [{ is_blocked: false, is_blocking: false, due_date: daysFromNow(5) }],
        1,
      ).label,
    ).toBe('Due Soon')
  })

  it('is On Track otherwise', () => {
    expect(
      projectStatus(
        [{ is_blocked: false, is_blocking: false, due_date: daysFromNow(30) }],
        1,
      ).label,
    ).toBe('On Track')
  })
})

describe('buildProjectStats', () => {
  const root = { is_blocked: false, is_blocking: false, due_date: null, parent_task_id: null }

  it('computes counts and progress', () => {
    const stats = buildProjectStats([root], 3)
    expect(stats.open).toBe(1)
    expect(stats.subtasks).toBe(0)
    expect(stats.done).toBe(3)
    expect(stats.progress).toBe(0.75)
    expect(stats.status.label).toBe('On Track')
  })

  it('progress is 0 when there are no tasks', () => {
    expect(buildProjectStats([], 0).progress).toBe(0)
  })

  it('counts root tasks as open and reports subtasks separately', () => {
    const stats = buildProjectStats(
      [root, { ...root, parent_task_id: 1 }, { ...root, parent_task_id: 1 }],
      1,
    )
    expect(stats.open).toBe(1)
    expect(stats.subtasks).toBe(2)
    expect(stats.done).toBe(1)
    // Every filed task stays in the denominator — the dashboard lane's ratio.
    expect(stats.progress).toBe(0.25)
  })

  it('weighs subtasks in the health label even though they are not counted as open', () => {
    const stats = buildProjectStats(
      [root, { ...root, parent_task_id: 1, is_blocking: true }],
      0,
    )
    expect(stats.open).toBe(1)
    expect(stats.status.label).toBe('Blocking')
  })

  it('treats a server-flagged orphan as a root', () => {
    const orphan = { ...root, parent_task_id: 99, is_effective_top_level: true }
    expect(buildProjectStats([orphan], 0)).toMatchObject({ open: 1, subtasks: 0 })
  })
})
