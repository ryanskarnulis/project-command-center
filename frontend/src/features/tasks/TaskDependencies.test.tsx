import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  listDependencies,
  listDependents,
  removeDependency,
} from '../../api/taskDependencies'
import type { Task } from '../../types/task'
import { TaskDependencies } from './TaskDependencies'

vi.mock('../../api/taskDependencies', () => ({
  addDependency: vi.fn(),
  listDependencies: vi.fn(),
  listDependents: vi.fn(),
  removeDependency: vi.fn(),
}))

const mockListDependencies = vi.mocked(listDependencies)
const mockListDependents = vi.mocked(listDependents)
const mockRemoveDependency = vi.mocked(removeDependency)

const task: Task = {
  id: 1,
  project_id: null,
  parent_task_id: null,
  title: 'Ship release',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
  is_blocked: true,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

describe('TaskDependencies', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListDependencies.mockResolvedValue([
      {
        id: 4,
        task_id: 1,
        depends_on_task_id: 2,
        depends_on_title: 'Finish review',
        depends_on_workflow_status: 'open',
        depends_on_done: false,
      },
    ])
    mockListDependents.mockResolvedValue([])
  })

  it('keeps a dependency visible when removal rejects', async () => {
    const user = userEvent.setup()
    mockRemoveDependency.mockRejectedValue(new Error('Remove failed'))
    render(
      <MemoryRouter>
        <TaskDependencies task={task} tasks={[task]} />
      </MemoryRouter>,
    )

    await user.click(
      await screen.findByRole('button', {
        name: 'Remove dependency Finish review',
      }),
    )

    await waitFor(() =>
      expect(mockRemoveDependency).toHaveBeenCalledWith(1, 4),
    )
    expect(screen.getByText('Finish review')).toBeInTheDocument()
    expect(mockListDependencies).toHaveBeenCalledTimes(1)
  })
})
