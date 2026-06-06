import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { compareByDue, compareTasks, dueStatus, formatDueDate } from './dates'

describe('dueStatus', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 5, 1)) // Jun 1 2026
  })
  afterEach(() => vi.useRealTimers())

  it('returns overdue for a past date', () => {
    expect(dueStatus('2026-05-30')).toBe('overdue')
  })

  it('returns overdue for yesterday', () => {
    expect(dueStatus('2026-05-31')).toBe('overdue')
  })

  it('returns today for today', () => {
    expect(dueStatus('2026-06-01')).toBe('today')
  })

  it('returns soon for a date within 3 days', () => {
    expect(dueStatus('2026-06-03')).toBe('soon')
  })

  it('returns none for a date 4+ days out', () => {
    expect(dueStatus('2026-06-05')).toBe('none')
  })

  it('returns none for a far future date', () => {
    expect(dueStatus('2026-06-30')).toBe('none')
  })

  it('returns none for null', () => {
    expect(dueStatus(null)).toBe('none')
  })

  it('respects custom soonDays', () => {
    expect(dueStatus('2026-06-07', 7)).toBe('soon')
    expect(dueStatus('2026-06-09', 7)).toBe('none')
  })
})

describe('formatDueDate', () => {
  it('returns empty string for null', () => {
    expect(formatDueDate(null)).toBe('')
  })

  it('formats a date as short month + day', () => {
    // toLocaleDateString output is locale-dependent; check it contains the day number
    const result = formatDueDate('2026-06-15')
    expect(result).toContain('15')
    expect(result.length).toBeGreaterThan(2)
  })
})

describe('compareTasks', () => {
  const t = (id: number, due_date: string | null, priority: string) => ({ id, due_date, priority })

  it('sorts earlier due dates before later ones regardless of priority', () => {
    const tasks = [t(1, '2026-06-10', 'low'), t(2, '2026-06-05', 'urgent')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([2, 1])
  })

  it('sorts tasks without a due date last', () => {
    const tasks = [t(1, null, 'urgent'), t(2, '2026-06-01', 'low')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([2, 1])
  })

  it('breaks equal due dates by priority (urgent before low)', () => {
    const tasks = [t(1, '2026-06-01', 'low'), t(2, '2026-06-01', 'urgent')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([2, 1])
  })

  it('priority order: urgent < high < medium < low', () => {
    const tasks = [t(4, '2026-06-01', 'low'), t(3, '2026-06-01', 'medium'), t(2, '2026-06-01', 'high'), t(1, '2026-06-01', 'urgent')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([1, 2, 3, 4])
  })

  it('breaks equal due + priority ties by id', () => {
    const tasks = [t(5, '2026-06-01', 'medium'), t(2, '2026-06-01', 'medium')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([2, 5])
  })

  it('null-vs-null breaks by priority then id', () => {
    const tasks = [t(2, null, 'low'), t(1, null, 'urgent')]
    expect([...tasks].sort(compareTasks).map((x) => x.id)).toEqual([1, 2])
  })
})

describe('compareByDue', () => {
  it('sorts earlier (more overdue) due dates first', () => {
    const tasks = [
      { id: 1, due_date: '2026-06-05' },
      { id: 2, due_date: '2026-05-30' },
      { id: 3, due_date: '2026-06-01' },
    ]
    expect([...tasks].sort(compareByDue).map((t) => t.id)).toEqual([2, 3, 1])
  })

  it('sorts tasks without a due date last', () => {
    const tasks = [
      { id: 1, due_date: null },
      { id: 2, due_date: '2026-06-01' },
      { id: 3, due_date: null },
    ]
    expect([...tasks].sort(compareByDue).map((t) => t.id)).toEqual([2, 1, 3])
  })

  it('breaks ties by id', () => {
    const tasks = [
      { id: 3, due_date: '2026-06-01' },
      { id: 1, due_date: '2026-06-01' },
    ]
    expect([...tasks].sort(compareByDue).map((t) => t.id)).toEqual([1, 3])
  })
})
