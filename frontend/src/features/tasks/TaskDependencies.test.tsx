import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
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

  function renderPanel() {
    render(
      <MemoryRouter>
        <TaskDependencies task={task} tasks={[task]} />
      </MemoryRouter>,
    )
    return screen.findByRole('button', {
      name: 'Remove dependency Finish review',
    })
  }

  it('surfaces the failure when removal rejects, keeping the row', async () => {
    const user = userEvent.setup()
    mockRemoveDependency.mockRejectedValue(new Error('Remove failed'))

    await user.click(await renderPanel())

    await waitFor(() => expect(mockRemoveDependency).toHaveBeenCalledWith(1, 4))
    expect(await screen.findByRole('alert')).toHaveTextContent('Remove failed')
    // Row stays and the list is not reloaded — the dependency still exists.
    expect(screen.getByText('Finish review')).toBeInTheDocument()
    expect(mockListDependencies).toHaveBeenCalledTimes(1)
  })

  it("prefers the API's detail over the generic status message", async () => {
    const user = userEvent.setup()
    mockRemoveDependency.mockRejectedValue(
      new ApiError(409, { detail: 'Dependency already removed' }),
    )

    await user.click(await renderPanel())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Dependency already removed',
    )
  })

  it('clears the error and reloads when a retry succeeds', async () => {
    const user = userEvent.setup()
    mockRemoveDependency.mockRejectedValueOnce(new Error('Remove failed'))
    mockRemoveDependency.mockResolvedValueOnce(undefined)
    const removeButton = await renderPanel()

    await user.click(removeButton)
    expect(await screen.findByRole('alert')).toHaveTextContent('Remove failed')

    mockListDependencies.mockResolvedValue([])
    await user.click(removeButton)

    await waitFor(() => expect(mockListDependencies).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('Finish review')).not.toBeInTheDocument()
  })
})
