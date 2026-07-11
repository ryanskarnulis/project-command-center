import { describe, expect, it, vi } from 'vitest'
import type { ToolCallRecord } from '../../types/agent'
import { describeToolCall, isMutation, undoFor } from './toolCalls'

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
