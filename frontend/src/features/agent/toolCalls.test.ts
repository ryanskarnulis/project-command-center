import { describe, expect, it, vi } from 'vitest'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, isMutation, linkFor, undoFor } from './toolCalls'

vi.mock('../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
}))

vi.mock('../../api/focus', () => ({
  removeFocusEntry: vi.fn(),
  restoreFocusEntry: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  deleteProject: vi.fn(),
  restoreProject: vi.fn(),
}))

function record(overrides: Partial<ToolCallRecord>): ToolCallRecord {
  return { tool: 'create_task', arguments: {}, result: null, error: null, ...overrides }
}

describe('describeToolCall', () => {
  it('names the entity from the result payload', () => {
    const call = record({
      tool: 'create_task',
      result: JSON.stringify({ id: 7, title: 'Fix VPN' }),
    })
    expect(describeToolCall(call)).toBe('Created task “Fix VPN”')
  })

  it('falls back to the arguments when the call failed', () => {
    const call = record({
      tool: 'create_task',
      arguments: { title: 'Fix VPN', prioirty: 'high' },
      error: 'Invalid arguments: prioirty: Extra inputs are not permitted',
    })
    expect(describeToolCall(call)).toBe('Created task “Fix VPN”')
  })

  it('reads the nested arguments of a run recorded before #411', () => {
    const call = record({
      tool: 'create_task',
      arguments: { data: { title: 'Fix VPN' } },
      error: 'Invalid arguments: data.title: Field required',
    })
    expect(describeToolCall(call)).toBe('Created task “Fix VPN”')
  })

  it('describes searches with the query', () => {
    const call = record({ tool: 'search', arguments: { query: 'vpn' } })
    expect(describeToolCall(call)).toBe('Searched for “vpn”')
  })

  it('humanizes unknown tools instead of hiding them', () => {
    expect(describeToolCall(record({ tool: 'future_tool' }))).toBe('future tool')
  })
})

describe('isMutation', () => {
  it('treats reads as non-mutations', () => {
    expect(isMutation(record({ tool: 'list_tasks' }))).toBe(false)
    expect(isMutation(record({ tool: 'search' }))).toBe(false)
    expect(isMutation(record({ tool: 'get_focus_plan' }))).toBe(false)
    expect(isMutation(record({ tool: 'ask_user' }))).toBe(false)
  })

  it('treats writes as mutations', () => {
    expect(isMutation(record({ tool: 'create_task' }))).toBe(true)
    expect(isMutation(record({ tool: 'trash_project' }))).toBe(true)
  })
})

describe('linkFor', () => {
  it('links task mutations to the task from the result id', () => {
    const result = JSON.stringify({ id: 7, title: 'X' })
    for (const tool of [
      'create_task',
      'update_task',
      'complete_task',
      'reopen_task',
      'restore_task',
    ]) {
      expect(linkFor(record({ tool, result }))).toBe('/tasks/7')
    }
  })

  it('links recurrence calls via the result id, falling back to the argument', () => {
    expect(
      linkFor(record({ tool: 'skip_occurrence', result: JSON.stringify({ id: 12 }) })),
    ).toBe('/tasks/12')
    expect(
      linkFor(
        record({ tool: 'stop_recurrence', arguments: { task_id: 5 }, result: 'ok' }),
      ),
    ).toBe('/tasks/5')
  })

  it('links project mutations to the project', () => {
    const result = JSON.stringify({ id: 3, name: 'P' })
    for (const tool of [
      'create_project',
      'update_project',
      'close_project',
      'reopen_project',
      'restore_project',
    ]) {
      expect(linkFor(record({ tool, result }))).toBe('/projects/3')
    }
  })

  it('links single-entity reads when an id is recoverable', () => {
    expect(
      linkFor(record({ tool: 'get_task', arguments: { task_id: 4 }, result: null })),
    ).toBe('/tasks/4')
    expect(
      linkFor(record({ tool: 'get_project', result: JSON.stringify({ id: 9 }) })),
    ).toBe('/projects/9')
  })

  it('links trash calls to the trash page, never the trashed entity', () => {
    expect(linkFor(record({ tool: 'trash_task', arguments: { task_id: 9 } }))).toBe(
      '/trash',
    )
    expect(
      linkFor(record({ tool: 'trash_project', arguments: { project_id: 2 } })),
    ).toBe('/trash')
  })

  it('returns null for collection reads, failures, and unrecoverable ids', () => {
    expect(linkFor(record({ tool: 'search', arguments: { query: 'x' } }))).toBeNull()
    expect(linkFor(record({ tool: 'list_tasks', result: '[]' }))).toBeNull()
    expect(linkFor(record({ tool: 'get_focus_plan', result: '{}' }))).toBeNull()
    expect(linkFor(record({ tool: 'create_task', error: 'boom' }))).toBeNull()
    expect(linkFor(record({ tool: 'create_task', result: 'not json' }))).toBeNull()
    expect(linkFor(record({ tool: 'update_task', result: '{"id": "7"}' }))).toBeNull()
  })

  it('reroutes undone rows to where the entity now lives', () => {
    const created = record({
      tool: 'create_task',
      result: JSON.stringify({ id: 7 }),
    })
    expect(linkFor(created, { undone: true })).toBe('/trash')

    const trashed = record({ tool: 'trash_task', arguments: { task_id: 9 } })
    expect(linkFor(trashed, { undone: true })).toBe('/tasks/9')

    const trashedProject = record({
      tool: 'trash_project',
      arguments: { project_id: 2 },
    })
    expect(linkFor(trashedProject, { undone: true })).toBe('/projects/2')
  })
})

describe('undoFor', () => {
  it('inverts create_task via its result id', async () => {
    const { deleteTask } = await import('../../api/tasks')
    const action = undoFor(
      record({ tool: 'create_task', result: JSON.stringify({ id: 42, title: 'X' }) }),
    )
    expect(action).not.toBeNull()
    expect(action?.label).toBe('Undo (move to trash)')
    await action?.perform()
    expect(vi.mocked(deleteTask)).toHaveBeenCalledWith(42)
  })

  it('inverts trash_task via its argument id (result is plain text)', async () => {
    const { restoreTask } = await import('../../api/tasks')
    const action = undoFor(
      record({
        tool: 'trash_task',
        arguments: { task_id: 9 },
        result:
          'Task 9 "X" and its subtasks moved to trash ' +
          '(undo with restore_task(9, restore_subtasks=True))',
      }),
    )
    // trash_task cascades over the subtree, so the undo must restore the
    // subtasks it trashed as well — a root-only restore is not an undo (#192).
    expect(action?.label).toBe('Undo (restore with subtasks)')
    await action?.perform()
    expect(vi.mocked(restoreTask)).toHaveBeenCalledWith(9, true)
  })

  it('inverts complete_task with a reopen', () => {
    const action = undoFor(
      record({ tool: 'complete_task', result: JSON.stringify({ id: 3 }) }),
    )
    expect(action?.label).toBe('Undo (reopen)')
  })

  it('does not call a recurring completion an undo', () => {
    // The successor spawned by the completion survives the reopen, so the
    // operation is not an inverse and must not be sold as one.
    const action = undoFor(
      record({
        tool: 'complete_task',
        result: JSON.stringify({
          id: 3,
          recurrence_id: 'r1',
          repeat_interval: { unit: 'week', every: 1 },
        }),
      }),
    )
    expect(action?.label).toBe('Reopen (keeps next occurrence)')
  })

  it('labels re-completing a recurring task as duplicate-free', () => {
    const action = undoFor(
      record({
        tool: 'reopen_task',
        result: JSON.stringify({
          id: 3,
          recurrence_id: 'r1',
          repeat_interval: { unit: 'week', every: 1 },
        }),
      }),
    )
    expect(action?.label).toBe('Complete again (no duplicate occurrence)')
  })

  it('keeps plain undo wording for a task whose series was stopped', () => {
    // recurrence_id outlives a stopped series; repeat_interval is what makes it live.
    const action = undoFor(
      record({
        tool: 'complete_task',
        result: JSON.stringify({ id: 3, recurrence_id: 'r1', repeat_interval: null }),
      }),
    )
    expect(action?.label).toBe('Undo (reopen)')
  })

  it('unskips a skipped occurrence using the argument id', async () => {
    // skip_occurrence returns the NEXT occurrence, so the id to restore is the
    // one that was skipped — from the arguments, not the result.
    const action = undoFor(
      record({
        tool: 'skip_occurrence',
        arguments: { task_id: 11 },
        result: JSON.stringify({ id: 12 }),
      }),
    )
    expect(action?.label).toBe('Undo (unskip)')
    const { restoreTask } = await import('../../api/tasks')
    await action?.perform()
    expect(vi.mocked(restoreTask)).toHaveBeenCalledWith(11)
  })

  it('offers no undo for failed calls, reads, or unreadable results', () => {
    expect(
      undoFor(record({ tool: 'create_task', error: 'boom', result: null })),
    ).toBeNull()
    expect(undoFor(record({ tool: 'list_tasks', result: '[]' }))).toBeNull()
    expect(undoFor(record({ tool: 'create_task', result: 'not json' }))).toBeNull()
    expect(undoFor(record({ tool: 'update_task', result: '{"id": 1}' }))).toBeNull()
    // stop_recurrence clears the interval across the whole series: no inverse.
    expect(
      undoFor(record({ tool: 'stop_recurrence', result: '{"id": 1}' })),
    ).toBeNull()
  })
})

describe('focus plan tools (#319)', () => {
  it('unpins a pin, removes a commitment and restores a removal', async () => {
    const { removeFocusEntry, restoreFocusEntry } = await import('../../api/focus')
    const pin = undoFor(
      record({ tool: 'pin_focus_task', arguments: { task_id: 3 }, result: JSON.stringify({ id: 12 }) }),
    )
    expect(pin?.label).toBe('Unpin')
    await pin?.perform()
    expect(vi.mocked(removeFocusEntry)).toHaveBeenCalledWith(12)

    const commitment = record({
      tool: 'add_focus_commitment',
      arguments: { title: 'Standup', start_time: '14:00', duration_minutes: 30 },
      result: JSON.stringify({ id: 13, title: 'Standup' }),
    })
    expect(describeToolCall(commitment)).toBe('Added “Standup” to the focus plan')
    expect(linkFor(commitment)).toBe('/focus')
    await undoFor(commitment)?.perform()
    expect(vi.mocked(removeFocusEntry)).toHaveBeenCalledWith(13)

    const removal = undoFor(
      record({ tool: 'remove_focus_entry', arguments: { entry_id: 14 }, result: 'Focus entry 14 removed' }),
    )
    await removal?.perform()
    expect(vi.mocked(restoreFocusEntry)).toHaveBeenCalledWith(14)
  })
})

describe('reorder_focus_pins (#394)', () => {
  it('has its own label, links to /focus and offers no undo', () => {
    const call = record({
      tool: 'reorder_focus_pins',
      arguments: { entry_ids: [12, 11] },
      result: JSON.stringify([{ id: 12 }, { id: 11 }]),
    })
    expect(describeToolCall(call)).toBe('Reordered the pins in the focus plan')
    expect(isMutation(call)).toBe(true)
    expect(linkFor(call)).toBe('/focus')
    // The prior order isn't in the record, so there's no inverse to offer.
    expect(undoFor(call)).toBeNull()
    expect(linkFor(record({ tool: 'reorder_focus_pins', error: 'boom' }))).toBeNull()
  })
})

describe('recurring Focus commitment tools (#426)', () => {
  it('label, link to /focus, and undo a skip or one-day edit by removing its row', () => {
    for (const tool of ['skip_focus_occurrence', 'edit_focus_occurrence']) {
      const call = record({
        tool,
        arguments: { series_id: 4, plan_date: '2026-10-21' },
        result: JSON.stringify({ id: 9 }),
      })
      expect(describeToolCall(call)).not.toBe(tool.replaceAll('_', ' '))
      expect(isMutation(call)).toBe(true)
      expect(linkFor(call)).toBe('/focus')
      expect(undoFor(call)?.label).toBe('Undo (remove)')
    }
  })

  it('links a series stop to /focus with no undo', () => {
    const call = record({
      tool: 'stop_focus_series',
      arguments: { series_id: 4, stop_date: '2026-11-01' },
      result: JSON.stringify({ id: 4 }),
    })
    expect(describeToolCall(call)).toBe('Ended a repeating commitment from a day on')
    expect(linkFor(call)).toBe('/focus')
    expect(undoFor(call)).toBeNull()
  })
})
