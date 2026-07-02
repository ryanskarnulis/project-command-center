import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  addDaysISO,
  compareByDue,
  compareTasks,
  dueStatus,
  formatDueDate,
  formatRelative,
  toISODate,
  todayISO,
} from './dates'

describe('todayISO / toISODate / addDaysISO', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 6, 15)) // Jul 15 2026
  })
  afterEach(() => vi.useRealTimers())

  it('formats today as YYYY-MM-DD with zero padding', () => {
    expect(todayISO()).toBe('2026-07-15')
  })

  it('formats a local Date', () => {
    expect(toISODate(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('adds days within a month', () => {
    expect(addDaysISO('2026-07-15', 1)).toBe('2026-07-16')
  })

  it('rolls over month and year boundaries', () => {
    expect(addDaysISO('2026-07-28', 7)).toBe('2026-08-04')
    expect(addDaysISO('2026-12-30', 7)).toBe('2027-01-06')
  })

  it('handles leap February', () => {
    expect(addDaysISO('2028-02-28', 1)).toBe('2028-02-29')
  })
})

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

describe('formatRelative', () => {
  const now = new Date('2026-06-19T12:00:00Z').getTime()
  const ago = (sec: number) => new Date(now - sec * 1000).toISOString()

  it('returns "just now" under a minute', () => {
    expect(formatRelative(ago(0), now)).toBe('just now')
    expect(formatRelative(ago(59), now)).toBe('just now')
  })

  it('formats minutes, hours, and days', () => {
    expect(formatRelative(ago(60), now)).toBe('1 minute ago')
    expect(formatRelative(ago(120), now)).toBe('2 minutes ago')
    expect(formatRelative(ago(3_600), now)).toBe('1 hour ago')
    expect(formatRelative(ago(86_400), now)).toBe('1 day ago')
    expect(formatRelative(ago(3 * 86_400), now)).toBe('3 days ago')
  })

  it('formats weeks, months, and years', () => {
    expect(formatRelative(ago(7 * 86_400), now)).toBe('1 week ago')
    expect(formatRelative(ago(30 * 86_400), now)).toBe('1 month ago')
    expect(formatRelative(ago(400 * 86_400), now)).toBe('1 year ago')
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
