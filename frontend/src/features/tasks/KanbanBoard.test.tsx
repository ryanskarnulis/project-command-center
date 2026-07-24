import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, type Mock, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import type { Task } from '../../types/task'
import { KanbanBoard } from './KanbanBoard'

afterEach(cleanup)

const base: Task = {
  id: 1,
  project_id: 1,
  parent_task_id: null,
  title: 'Task',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-06-01T00:00:00',
  updated_at: '2026-06-01T00:00:00',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

function task(over: Partial<Task>): Task {
  return { ...base, ...over }
}

function renderBoard(
  active: Task[],
  completed: Task[] = [],
  onSetStatus: Mock = vi.fn(() => Promise.resolve()),
) {
  render(
    <MemoryRouter>
      <KanbanBoard
        activeTasks={active}
        completedTasks={completed}
        isGlobal={false}
        onSetStatus={onSetStatus}
        onUpdate={vi.fn(() => Promise.resolve())}
      />
    </MemoryRouter>,
  )
  return onSetStatus
}

/** Opens a card's status chip and picks a target status from its menu. */
async function moveViaStatusChip(
  user: ReturnType<typeof userEvent.setup>,
  fromLabel: string,
  toLabel: string,
) {
  await user.click(screen.getByRole('button', { name: `Status: ${fromLabel}` }))
  await user.click(
    within(screen.getByRole('dialog')).getByRole('button', { name: toLabel }),
  )
}

describe('KanbanBoard', () => {
  it('lays tasks into columns by workflow_status', () => {
    renderBoard(
      [
        task({ id: 1, title: 'Open one', workflow_status: 'open' }),
        task({ id: 2, title: 'Doing it', workflow_status: 'in_progress' }),
      ],
      [task({ id: 3, title: 'Finished', workflow_status: 'done' })],
    )
    const open = screen.getByRole('region', { name: 'Open' })
    const inProgress = screen.getByRole('region', { name: 'In progress' })
    const done = screen.getByRole('region', { name: 'Done' })
    expect(within(open).getByText('Open one')).toBeInTheDocument()
    expect(within(inProgress).getByText('Doing it')).toBeInTheDocument()
    expect(within(done).getByText('Finished')).toBeInTheDocument()
  })

  it('routes a move via the card status chip to onSetStatus', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard([
      task({ id: 1, title: 'Open one', workflow_status: 'open' }),
    ])
    await moveViaStatusChip(user, 'Open', 'In progress')
    expect(onSetStatus).toHaveBeenCalledTimes(1)
    expect(onSetStatus.mock.calls[0][0].id).toBe(1)
    expect(onSetStatus.mock.calls[0][1]).toBe('in_progress')
  })

  it('allows starting a blocked task (blocking gates completion, not starting)', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard([
      task({ id: 1, title: 'Blocked one', is_blocked: true }),
    ])
    await moveViaStatusChip(user, 'Open', 'In progress')
    expect(onSetStatus).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      'in_progress',
    )
  })

  it('refuses completing a blocked task', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard([
      task({
        id: 1,
        title: 'Blocked one',
        workflow_status: 'in_progress',
        is_blocked: true,
      }),
    ])
    await moveViaStatusChip(user, 'In progress', 'Done')
    expect(onSetStatus).not.toHaveBeenCalled()
  })

  it('allows moving a blocked task back to Open', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard([
      task({
        id: 1,
        title: 'Blocked one',
        workflow_status: 'in_progress',
        is_blocked: true,
      }),
    ])
    await moveViaStatusChip(user, 'In progress', 'Open')
    expect(onSetStatus).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1 }),
      'open',
    )
  })

  it('routes a done-column task back out via onSetStatus', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard(
      [],
      [task({ id: 9, title: 'Finished', workflow_status: 'done' })],
    )
    await moveViaStatusChip(user, 'Done', 'Open')
    expect(onSetStatus).toHaveBeenCalledWith(
      expect.objectContaining({ id: 9, workflow_status: 'done' }),
      'open',
    )
  })

  // The rejecting handlers below are PLAIN functions, not vi.fn(): Vitest
  // observes a mock's returned promise (for settledResults), which attaches a
  // handler and hides the very unhandled rejection these tests guard against.
  // A plain function leaves the promise unobserved, so the setup.ts backstop
  // (process 'unhandledRejection') fails the test if a handler leaks it.

  // A failed move must not leak — the swallow lives in move()'s try/catch.
  it('swallows a failing move (onSetStatus rejects) without leaking', async () => {
    const user = userEvent.setup()
    let calls = 0
    const onSetStatus = () => {
      calls += 1
      return Promise.reject(new Error('boom'))
    }
    render(
      <MemoryRouter>
        <KanbanBoard
          activeTasks={[task({ id: 1, title: 'Open one', workflow_status: 'open' })]}
          completedTasks={[]}
          isGlobal={false}
          onSetStatus={onSetStatus}
          onUpdate={vi.fn(() => Promise.resolve())}
        />
      </MemoryRouter>,
    )
    await moveViaStatusChip(user, 'Open', 'In progress')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(calls).toBe(1)
    // The card stays interactive after the failed move (pending cleared).
    expect(
      screen.getByRole('button', { name: 'Status: Open' }),
    ).toBeInTheDocument()
  })

  // The card chip edit calls onUpdate directly (not via move()); it must be
  // wrapped so a rejected patch doesn't leak. Covers KanbanBoard.tsx:115.
  it('swallows a failing chip edit (onUpdate rejects) without leaking', async () => {
    const user = userEvent.setup()
    const patches: Partial<Task>[] = []
    const onUpdate = (_t: Task, patch: Partial<Task>) => {
      patches.push(patch)
      return Promise.reject(new Error('boom'))
    }
    render(
      <MemoryRouter>
        <KanbanBoard
          activeTasks={[
            task({ id: 1, title: 'Open one', workflow_status: 'open', priority: 'medium' }),
          ]}
          completedTasks={[]}
          isGlobal={false}
          onSetStatus={vi.fn(() => Promise.resolve())}
          onUpdate={onUpdate}
        />
      </MemoryRouter>,
    )
    await user.click(screen.getByRole('button', { name: 'Priority: medium' }))
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /high/i }),
    )
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(patches).toEqual([{ priority: 'high' }])
  })
})
