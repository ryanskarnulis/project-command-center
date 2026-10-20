import { describe, expect, it } from 'vitest'
import type { ScheduledBlock } from '../../types/focus'
import { dropPin, floatingPinOrder, shiftPin } from './pinOrder'

function block(overrides: Partial<ScheduledBlock>): ScheduledBlock {
  return {
    task_id: 1,
    title: 'task',
    project_id: 1,
    start_time: '09:00',
    end_time: '09:30',
    start_day_offset: 0,
    end_day_offset: 0,
    estimated_minutes: 30,
    estimate_assumed: false,
    priority: 'medium',
    workflow_status: 'open',
    due_date: null,
    due_signal: 'none',
    is_recurring: false,
    reason: '',
    parent_task_id: null,
    parent_title: null,
    entry_id: null,
    pinned: false,
    fixed: false,
    pin_order: null,
    ...overrides,
  }
}

describe('floatingPinOrder', () => {
  it('lists floating pins by pin order, skipping ranked and fixed blocks', () => {
    const order = floatingPinOrder([
      block({ task_id: 1, entry_id: 11, pinned: true, pin_order: 1 }),
      block({ task_id: 2 }),
      block({ task_id: 3, entry_id: 13, pinned: true, fixed: true }),
      block({ task_id: 4, entry_id: 14, pinned: true, pin_order: 0 }),
    ])
    expect(order).toEqual([14, 11])
  })
})

describe('shiftPin', () => {
  it('moves one slot and refuses to move past either end', () => {
    expect(shiftPin([1, 2, 3], 2, -1)).toEqual([2, 1, 3])
    expect(shiftPin([1, 2, 3], 2, 1)).toEqual([1, 3, 2])
    expect(shiftPin([1, 2, 3], 1, -1)).toBeNull()
    expect(shiftPin([1, 2, 3], 3, 1)).toBeNull()
    expect(shiftPin([1, 2, 3], 9, 1)).toBeNull()
    expect(shiftPin([1, 2, 3], 2, 0)).toBeNull()
  })
})

describe('dropPin', () => {
  it("takes the target's slot, shifting the pins between", () => {
    expect(dropPin([1, 2, 3], 3, 1)).toEqual([3, 1, 2])
    expect(dropPin([1, 2, 3], 1, 3)).toEqual([2, 3, 1])
    expect(dropPin([1, 2, 3], 2, 2)).toBeNull()
    expect(dropPin([1, 2, 3], 2, 9)).toBeNull()
  })
})
