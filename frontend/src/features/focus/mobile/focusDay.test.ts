import { describe, expect, it } from 'vitest'
import type { BlockedTask, ScheduledBlock } from '../../../types/focus'
import {
  blockingCounts,
  buildDayShape,
  formatClockDuration,
  formatClockTime,
  proportionalHeight,
  rowContext,
  rowSignal,
  tailHeight,
} from './focusDay'

function block(overrides: Partial<ScheduledBlock> = {}): ScheduledBlock {
  return {
    task_id: 1,
    title: 'Cut over DNS to the new resolver',
    project_id: 1,
    start_time: '09:00',
    end_time: '11:00',
    start_day_offset: 0,
    end_day_offset: 0,
    estimated_minutes: 120,
    estimate_assumed: false,
    priority: 'urgent',
    workflow_status: 'open',
    due_date: null,
    due_signal: 'none',
    is_recurring: false,
    reason: 'open · urgent',
    parent_task_id: null,
    parent_title: null,
    ...overrides,
  }
}

describe('formatClockDuration', () => {
  it('reads a day plan in hours and minutes together', () => {
    expect(formatClockDuration(165)).toBe('2h 45m')
    expect(formatClockDuration(120)).toBe('2h')
    expect(formatClockDuration(45)).toBe('45m')
    expect(formatClockDuration(0)).toBe('0m')
    expect(formatClockDuration(-5)).toBe('0m')
  })
})

describe('formatClockTime', () => {
  it('wraps a session that runs past midnight', () => {
    expect(formatClockTime(540)).toBe('09:00')
    expect(formatClockTime(735)).toBe('12:15')
    expect(formatClockTime(1455)).toBe('00:15')
  })
})

describe('row heights', () => {
  // The duration-is-height contract, at the sizes the handoff tabulates.
  it.each([
    [15, 57],
    [30, 67],
    [45, 76],
    [60, 86],
    [120, 124],
    [216, 184],
    [600, 184],
  ])('gives %d minutes %dpx', (minutes, height) => {
    expect(proportionalHeight(minutes)).toBe(height)
  })

  it('floors and caps the free tail', () => {
    expect(tailHeight(0)).toBe(76)
    expect(tailHeight(165)).toBe(140)
    expect(tailHeight(60)).toBe(86)
  })
})

describe('buildDayShape', () => {
  it('places blocks end to end from the session start and sizes the free tail', () => {
    const shape = buildDayShape({
      finished: [],
      scheduled: [block({ task_id: 1, estimated_minutes: 120 }), block({ task_id: 2, estimated_minutes: 30 })],
      dayStart: 540,
      capacity: 360,
      elapsed: 0,
    })

    expect(shape.rows.map((row) => row.start)).toEqual([540, 660])
    expect(shape.rows.map((row) => row.kind)).toEqual(['current', 'upcoming'])
    expect(shape.dayEnd).toBe(690)
    expect(shape.spent).toBe(150)
    expect(shape.free).toBe(210)
    expect(shape.overage).toBe(0)
    expect(shape.shift).toBe(0)
  })

  it('collapses finished work to a receipt and starts the plan after it', () => {
    const shape = buildDayShape({
      finished: [{ taskId: 9, title: 'Renew the wildcard TLS cert', minutes: 30, plannedMinutes: 30 }],
      scheduled: [block({ task_id: 1, estimated_minutes: 120 })],
      dayStart: 540,
      capacity: 360,
      elapsed: 0,
    })

    expect(shape.rows[0]).toMatchObject({ kind: 'done', start: 540, height: 44 })
    expect(shape.rows[1]).toMatchObject({ kind: 'current', start: 570 })
    expect(shape.currentIndex).toBe(1)
    expect(shape.spent).toBe(150)
  })

  it('grows the current block past its estimate and pushes every later row', () => {
    const shape = buildDayShape({
      finished: [],
      scheduled: [block({ task_id: 1, estimated_minutes: 120 }), block({ task_id: 2, estimated_minutes: 30 })],
      dayStart: 540,
      capacity: 360,
      elapsed: 142,
    })

    expect(shape.overage).toBe(22)
    expect(shape.rows[0].duration).toBe(142)
    // The row after it moved by exactly the overage, which is what the notice says.
    expect(shape.rows[1].start).toBe(682)
    expect(shape.shift).toBe(22)
    expect(shape.free).toBe(188)
  })

  it('reports a day past its capacity as negative free time', () => {
    const shape = buildDayShape({
      finished: [],
      scheduled: [block({ estimated_minutes: 240 })],
      dayStart: 540,
      capacity: 180,
      elapsed: 0,
    })

    expect(shape.free).toBe(-60)
    expect(shape.segments.over).toBeCloseTo(25)
    expect(shape.segments.planned).toBeCloseTo(75)
    expect(shape.segments.done).toBe(0)
  })

  it('splits the capacity bar into spent, scheduled and past-capacity', () => {
    const shape = buildDayShape({
      finished: [{ taskId: 9, title: 'Renew the cert', minutes: 60, plannedMinutes: 60 }],
      scheduled: [block({ estimated_minutes: 60 })],
      dayStart: 540,
      capacity: 240,
      elapsed: 0,
    })

    expect(shape.segments.done).toBeCloseTo(25)
    expect(shape.segments.planned).toBeCloseTo(25)
    expect(shape.segments.over).toBe(0)
  })

  it('has no current block once the day is clear', () => {
    const shape = buildDayShape({
      finished: [{ taskId: 9, title: 'Renew the cert', minutes: 30, plannedMinutes: 30 }],
      scheduled: [],
      dayStart: 540,
      capacity: 360,
      elapsed: 0,
    })

    expect(shape.currentIndex).toBe(-1)
    expect(shape.overage).toBe(0)
    expect(shape.dayEnd).toBe(570)
  })
})

describe('rowSignal', () => {
  it('leads with the loudest true fact, and only one', () => {
    expect(rowSignal('urgent', 'open', 'overdue')).toEqual({ label: 'Overdue', tone: 'danger' })
    expect(rowSignal('urgent', 'in_progress', 'due_today')).toEqual({ label: 'Urgent', tone: 'danger' })
    expect(rowSignal('high', 'in_progress', 'due_today')).toEqual({ label: 'In progress', tone: 'workflow' })
    expect(rowSignal('high', 'open', 'due_today')).toEqual({ label: 'High', tone: 'warning' })
    expect(rowSignal('medium', 'open', 'due_today')).toEqual({ label: 'Due today', tone: 'live' })
    expect(rowSignal('medium', 'open', 'none')).toBeNull()
  })
})

describe('rowContext', () => {
  it('spends its one slot on the strongest context, then the duration', () => {
    expect(rowContext(block({ estimated_minutes: 120 }), 2)).toBe('Blocks 2 · 2h')
    expect(rowContext(block({ estimated_minutes: 15, parent_title: 'Decommission the old NAS array' }), 0)).toBe(
      'Part of Decommission the old NAS array · 15m',
    )
    expect(rowContext(block({ estimated_minutes: 30, is_recurring: true }), 0)).toBe('30m · Repeats')
    expect(rowContext(block({ estimated_minutes: 30, estimate_assumed: true }), 0)).toBe('30m est.')
    expect(rowContext(block({ estimated_minutes: 30 }), 0)).toBe('30m')
  })
})

describe('blockingCounts', () => {
  it('counts how many blocked tasks each scheduled block is holding up', () => {
    const blocked: BlockedTask[] = [
      {
        task_id: 5,
        title: 'Decommission the old NAS array',
        project_id: 1,
        priority: 'medium',
        due_date: null,
        blocking_tasks: [{ task_id: 1, title: 'Cut over DNS', workflow_status: 'open' }],
      },
      {
        task_id: 6,
        title: 'Retire the old tower',
        project_id: 1,
        priority: 'low',
        due_date: null,
        blocking_tasks: [
          { task_id: 1, title: 'Cut over DNS', workflow_status: 'open' },
          { task_id: 2, title: 'Export volumes', workflow_status: 'open' },
        ],
      },
    ]

    expect(blockingCounts(blocked).get(1)).toBe(2)
    expect(blockingCounts(blocked).get(2)).toBe(1)
    expect(blockingCounts(blocked).get(9)).toBeUndefined()
  })
})
