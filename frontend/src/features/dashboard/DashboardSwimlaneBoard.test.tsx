import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectOpenTasksRow } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { DashboardSwimlaneBoard } from './DashboardSwimlaneBoard'

vi.mock('../../api/tasks', () => ({
  listCompletedTasks: vi.fn(() => Promise.resolve([])),
  reopenTask: vi.fn(),
}))

afterEach(cleanup)

const project: ProjectOpenTasksRow = {
  project_id: 1,
  project_name: 'Alpha',
  open_task_count: 1,
}

const task: Task = {
  id: 1,
  project_id: 1,
  parent_task_id: null,
  title: 'Move me',
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
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

function dataTransfer(): DataTransfer {
  let value = ''
  return {
    effectAllowed: 'none',
    setData: vi.fn((_type: string, next: string) => {
      value = next
    }),
    getData: vi.fn(() => value),
  } as unknown as DataTransfer
}

function renderBoard(activeTask: Task, onSetStatus = vi.fn(() => Promise.resolve())) {
  render(
    <MemoryRouter>
      <DashboardSwimlaneBoard
        projects={[project]}
        tasks={[activeTask]}
        signal={null}
        onSetStatus={onSetStatus}
        onUpdate={vi.fn(() => Promise.resolve())}
      />
    </MemoryRouter>,
  )
  return onSetStatus
}

describe('DashboardSwimlaneBoard drag moves', () => {
  it('moves a task between status columns within its project lane', async () => {
    const onSetStatus = renderBoard(task)
    const transfer = dataTransfer()
    const card = screen.getByRole('link', { name: 'Move me' }).closest('li')
    if (!card) throw new Error('Expected task card wrapper')

    fireEvent.dragStart(card, { dataTransfer: transfer })
    fireEvent.drop(screen.getByRole('region', { name: 'Alpha In progress' }), {
      dataTransfer: transfer,
    })

    await waitFor(() => expect(onSetStatus).toHaveBeenCalledWith(task, 'in_progress'))
  })

  it('keeps a dependency-blocked task out of In progress', async () => {
    const blocked = { ...task, is_blocked: true }
    const onSetStatus = renderBoard(blocked)
    const transfer = dataTransfer()
    const card = screen.getByRole('link', { name: 'Move me' }).closest('li')
    if (!card) throw new Error('Expected task card wrapper')

    fireEvent.dragStart(card, { dataTransfer: transfer })
    fireEvent.drop(screen.getByRole('region', { name: 'Alpha In progress' }), {
      dataTransfer: transfer,
    })

    await waitFor(() => expect(onSetStatus).not.toHaveBeenCalled())
  })
})
