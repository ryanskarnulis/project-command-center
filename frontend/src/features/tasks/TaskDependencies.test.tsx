import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import {
  addDependency,
  listDependencies,
  listDependents,
  removeDependency,
} from '../../api/taskDependencies'
import type { Task, TaskDependency } from '../../types/task'
import { TaskDependencies } from './TaskDependencies'

vi.mock('../../api/taskDependencies', () => ({
  addDependency: vi.fn(),
  listDependencies: vi.fn(),
  listDependents: vi.fn(),
  removeDependency: vi.fn(),
}))

const mockAddDependency = vi.mocked(addDependency)
const mockListDependencies = vi.mocked(listDependencies)
const mockListDependents = vi.mocked(listDependents)
const mockRemoveDependency = vi.mocked(removeDependency)

/**
 * Dispatch two clicks inside a single `act`, so React cannot re-render — and
 * therefore cannot disable the control — between them. Only a synchronous
 * re-entry guard can stop the second dispatch.
 */
function clickTwiceInOneTick(el: HTMLElement) {
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
}

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

// A candidate blocker offered by the "Add dependency" select.
const blocker: Task = { ...task, id: 3, title: 'Draft plan', is_blocked: false }

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

  function renderPanel(tasks: Task[] = [task]) {
    render(
      <MemoryRouter>
        <TaskDependencies task={task} tasks={tasks} />
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

  it('adds once when Add is clicked twice before the request settles', async () => {
    let resolveAdd: (dependency: TaskDependency) => void = () => {}
    mockAddDependency.mockImplementation(
      () =>
        new Promise<TaskDependency>((resolve) => {
          resolveAdd = resolve
        }),
    )
    await renderPanel([task, blocker])

    const select = screen.getByLabelText('Add dependency')
    fireEvent.change(select, { target: { value: String(blocker.id) } })
    clickTwiceInOneTick(screen.getByRole('button', { name: 'Add' }))

    expect(mockAddDependency).toHaveBeenCalledTimes(1)
    expect(mockAddDependency).toHaveBeenCalledWith(task.id, blocker.id)
    expect(screen.getByRole('button', { name: 'Adding…' })).toBeDisabled()
    expect(select).toBeDisabled()

    resolveAdd({
      id: 6,
      task_id: task.id,
      depends_on_task_id: blocker.id,
      depends_on_title: blocker.title,
      depends_on_workflow_status: 'open',
      depends_on_done: false,
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument(),
    )
    expect(select).toBeEnabled()
    expect(mockAddDependency).toHaveBeenCalledTimes(1)
  })

  it('re-enables the add controls after a failed add', async () => {
    mockAddDependency.mockRejectedValue(
      new ApiError(409, { detail: 'Dependency already exists' }),
    )
    const user = userEvent.setup()
    await renderPanel([task, blocker])

    await user.selectOptions(
      screen.getByLabelText('Add dependency'),
      String(blocker.id),
    )
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Dependency already exists',
    )
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled()
    expect(screen.getByLabelText('Add dependency')).toBeEnabled()

    // Still retryable: the guard was released on the failure path.
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(mockAddDependency).toHaveBeenCalledTimes(2)
  })

  it('removes once when a row is clicked twice, leaving other rows usable', async () => {
    mockListDependencies.mockResolvedValue([
      {
        id: 4,
        task_id: 1,
        depends_on_task_id: 2,
        depends_on_title: 'Finish review',
        depends_on_workflow_status: 'open',
        depends_on_done: false,
      },
      {
        id: 5,
        task_id: 1,
        depends_on_task_id: 3,
        depends_on_title: 'Draft plan',
        depends_on_workflow_status: 'open',
        depends_on_done: false,
      },
    ])
    let resolveRemove: () => void = () => {}
    mockRemoveDependency.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRemove = resolve
        }),
    )
    const removeButton = await renderPanel()

    clickTwiceInOneTick(removeButton)

    expect(mockRemoveDependency).toHaveBeenCalledTimes(1)
    expect(mockRemoveDependency).toHaveBeenCalledWith(1, 4)
    expect(removeButton).toBeDisabled()
    // Per-row guard: the other dependency is still removable.
    expect(
      screen.getByRole('button', { name: 'Remove dependency Draft plan' }),
    ).toBeEnabled()

    resolveRemove()
    await waitFor(() => expect(mockListDependencies).toHaveBeenCalledTimes(2))
    expect(mockRemoveDependency).toHaveBeenCalledTimes(1)
  })

  it('re-enables the remove control after a failed removal', async () => {
    const user = userEvent.setup()
    mockRemoveDependency.mockRejectedValue(new Error('Remove failed'))
    const removeButton = await renderPanel()

    await user.click(removeButton)

    expect(await screen.findByRole('alert')).toHaveTextContent('Remove failed')
    expect(removeButton).toBeEnabled()
  })
})
