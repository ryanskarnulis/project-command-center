import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildAxis } from './ganttAxis'
import type { GanttBar } from './ganttModel'

function bar(overrides: Partial<GanttBar> & { id: number }): GanttBar {
  return {
    name: `Bar ${overrides.id}`,
    projectId: 1,
    start: '2026-06-01',
    end: '2026-06-01',
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

describe('buildAxis', () => {
  it('returns null with no bars', () => {
    expect(buildAxis([], 'day')).toBeNull()
  })

  describe('day zoom', () => {
    it('one column per calendar day across the span, inclusive', () => {
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-01', end: '2026-06-03' })],
        'day',
      )!
      expect(axis.columns).toHaveLength(3)
      expect(axis.columns.map((c) => c.iso)).toEqual([
        '2026-06-01',
        '2026-06-02',
        '2026-06-03',
      ])
      expect(axis.daysPerColumn).toBe(1)
    })

    it('maps a date to its day index and clamps out-of-range', () => {
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-01', end: '2026-06-05' })],
        'day',
      )!
      expect(axis.columnOf('2026-06-03')).toBe(2)
      expect(axis.columnOf('2026-05-01')).toBe(0) // before span -> first
      expect(axis.columnOf('2026-07-01')).toBe(4) // after span -> last
    })

    it('flags weekends and labels month boundaries', () => {
      // 2026-06-30 is a Tuesday; 07-01 starts a new month.
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-30', end: '2026-07-01' })],
        'day',
      )!
      expect(axis.columns[0].groupLabel).toBe('Jun') // first cell always labelled
      expect(axis.columns[1].groupLabel).toBe('Jul') // month rollover
      // 2026-06-06 is a Saturday.
      const wk = buildAxis(
        [bar({ id: 2, start: '2026-06-05', end: '2026-06-07' })],
        'day',
      )!
      expect(wk.columns.map((c) => c.isWeekend)).toEqual([false, true, true])
    })
  })

  describe('week zoom', () => {
    it('buckets days into Monday-anchored weeks', () => {
      // 2026-06-01 is a Monday. Span Mon Jun 1 -> Wed Jun 17 spans 3 weeks.
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-01', end: '2026-06-17' })],
        'week',
      )!
      expect(axis.columns.map((c) => c.iso)).toEqual([
        '2026-06-01',
        '2026-06-08',
        '2026-06-15',
      ])
      expect(axis.daysPerColumn).toBe(7)
      // Every day within a week maps to that week's column.
      expect(axis.columnOf('2026-06-07')).toBe(0) // Sunday, still week 0
      expect(axis.columnOf('2026-06-08')).toBe(1) // next Monday
      expect(axis.columnOf('2026-06-17')).toBe(2)
    })

    it('anchors the first column to the Monday on/before the span start', () => {
      // 2026-06-03 is a Wednesday; its week starts Mon 2026-06-01.
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-03', end: '2026-06-03' })],
        'week',
      )!
      expect(axis.columns[0].iso).toBe('2026-06-01')
    })
  })

  describe('month zoom', () => {
    it('one column per calendar month across the span', () => {
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-06-15', end: '2026-08-02' })],
        'month',
      )!
      expect(axis.columns.map((c) => c.iso)).toEqual([
        '2026-06-01',
        '2026-07-01',
        '2026-08-01',
      ])
      expect(axis.columns.map((c) => c.label)).toEqual(['Jun', 'Jul', 'Aug'])
      // Month end dates respect varying month lengths.
      expect(axis.columns[0].endIso).toBe('2026-06-30')
      expect(axis.columns[1].endIso).toBe('2026-07-31')
    })

    it('maps any day in a month to that month column and crosses a year', () => {
      const axis = buildAxis(
        [bar({ id: 1, start: '2026-12-10', end: '2027-01-20' })],
        'month',
      )!
      expect(axis.columns.map((c) => c.iso)).toEqual(['2026-12-01', '2027-01-01'])
      expect(axis.columnOf('2026-12-31')).toBe(0)
      expect(axis.columnOf('2027-01-05')).toBe(1)
      expect(axis.columns[1].groupLabel).toBe('2027') // year rollover label
    })
  })

  describe('todayIdx', () => {
    beforeEach(() => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-06-10T09:00:00'))
    })
    afterEach(() => vi.useRealTimers())

    it('finds today’s column when in span, else -1', () => {
      const inSpan = buildAxis(
        [bar({ id: 1, start: '2026-06-08', end: '2026-06-12' })],
        'day',
      )!
      expect(inSpan.todayIdx).toBe(2) // 2026-06-10
      const outSpan = buildAxis(
        [bar({ id: 2, start: '2026-07-01', end: '2026-07-03' })],
        'day',
      )!
      expect(outSpan.todayIdx).toBe(-1)
      // At week zoom today lands in the week column that contains it.
      const wk = buildAxis(
        [bar({ id: 3, start: '2026-06-08', end: '2026-06-20' })],
        'week',
      )!
      expect(wk.todayIdx).toBe(0) // week of Mon 2026-06-08
    })
  })
})
