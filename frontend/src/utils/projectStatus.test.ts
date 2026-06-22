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
  it('computes counts and progress', () => {
    const stats = buildProjectStats(
      [{ is_blocked: false, is_blocking: false, due_date: null }],
      3,
    )
    expect(stats.open).toBe(1)
    expect(stats.done).toBe(3)
    expect(stats.progress).toBe(0.75)
    expect(stats.status.label).toBe('On Track')
  })

  it('progress is 0 when there are no tasks', () => {
    expect(buildProjectStats([], 0).progress).toBe(0)
  })
})
