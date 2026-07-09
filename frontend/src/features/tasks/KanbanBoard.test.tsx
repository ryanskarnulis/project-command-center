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
  review_status: 'accepted',
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  confidence: null,
  assignee_hint: null,
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

  it('refuses moving a blocked task into In progress', async () => {
    const user = userEvent.setup()
    const onSetStatus = renderBoard([
      task({ id: 1, title: 'Blocked one', is_blocked: true }),
    ])
    await moveViaStatusChip(user, 'Open', 'In progress')
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
})
