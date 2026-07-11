import { describe, expect, it, vi } from 'vitest'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, isMutation, linkFor, undoFor } from './toolCalls'

vi.mock('../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
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
        result: 'Task 9 "X" moved to trash (undo with restore_task)',
      }),
    )
    expect(action?.label).toBe('Undo (restore)')
    await action?.perform()
    expect(vi.mocked(restoreTask)).toHaveBeenCalledWith(9)
  })

  it('inverts complete_task with a reopen', () => {
    const action = undoFor(
      record({ tool: 'complete_task', result: JSON.stringify({ id: 3 }) }),
    )
    expect(action?.label).toBe('Undo (reopen)')
  })

  it('offers no undo for failed calls, reads, or unreadable results', () => {
    expect(
      undoFor(record({ tool: 'create_task', error: 'boom', result: null })),
    ).toBeNull()
    expect(undoFor(record({ tool: 'list_tasks', result: '[]' }))).toBeNull()
    expect(undoFor(record({ tool: 'create_task', result: 'not json' }))).toBeNull()
    expect(undoFor(record({ tool: 'update_task', result: '{"id": 1}' }))).toBeNull()
  })
})
