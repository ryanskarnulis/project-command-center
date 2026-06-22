import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
import { listDependencies, listDependents } from '../../api/taskDependencies'
import { getSubtasks, getTask, getTaskSeries, listAllTasks, skipOccurrence, stopRecurrence, updateTask } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskDetailPage } from './TaskDetailPage'

vi.mock('../../api/tasks', () => ({
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getSubtasks: vi.fn(),
  getTask: vi.fn(),
  getTaskSeries: vi.fn(),
  listAllTasks: vi.fn(),
  skipOccurrence: vi.fn(),
  stopRecurrence: vi.fn(),
  updateTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
}))

vi.mock('../../api/taskDependencies', () => ({
  addDependency: vi.fn(),
  listDependencies: vi.fn(),
  listDependents: vi.fn(),
  removeDependency: vi.fn(),
}))

const baseTask: Task = {
  id: 7,
  project_id: 1,
  inbox_item_id: null,
  parent_task_id: null,
  title: 'Water the plants',
  description: null,
  review_status: 'accepted',
  workflow_status: 'open',
  priority: 'medium',
  due_date: '2026-06-01',
  estimated_minutes: null,
  repeat_interval: { unit: 'week', every: 1 },
  recurrence_id: 'series-abc',
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

const project: Project = {
  id: 1,
  name: 'Home',
  description: null,
  system_key: null,
  is_protected: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
}

const mockGetTask = vi.mocked(getTask)
const mockGetSubtasks = vi.mocked(getSubtasks)
const mockListAllTasks = vi.mocked(listAllTasks)
const mockListProjects = vi.mocked(listProjects)
const mockListDependencies = vi.mocked(listDependencies)
const mockListDependents = vi.mocked(listDependents)
const mockUpdateTask = vi.mocked(updateTask)
const mockSkipOccurrence = vi.mocked(skipOccurrence)
const mockGetTaskSeries = vi.mocked(getTaskSeries)
const mockStopRecurrence = vi.mocked(stopRecurrence)

function renderDetail(task: Task) {
  mockGetTask.mockResolvedValue(task)
  mockListAllTasks.mockResolvedValue([task])
  return render(
    <MemoryRouter initialEntries={['/tasks/7']}>
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Recurrence UI', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetSubtasks.mockResolvedValue([])
    mockListProjects.mockResolvedValue([project])
    mockListDependencies.mockResolvedValue([])
    mockListDependents.mockResolvedValue([])
    mockUpdateTask.mockImplementation(async (_id, patch) => ({ ...baseTask, ...patch }))
  })

  afterEach(cleanup)

  it('renders the repeat field, disabled when there is no due date', async () => {
    renderDetail({ ...baseTask, due_date: null, repeat_interval: null })

    const repeat = await screen.findByLabelText('Repeat')
    expect(repeat).toBeDisabled()
  })

  it('enables the repeat field once a due date is set', async () => {
    renderDetail(baseTask)

    const repeat = await screen.findByLabelText('Repeat')
    expect(repeat).toBeEnabled()
    await waitFor(() => expect(repeat).toHaveValue('weekly'))
  })

  it('shows EditScopeModal when editing a recurring task and forwards the scope', async () => {
    const user = userEvent.setup()
    renderDetail(baseTask)

    const title = await screen.findByLabelText('Task title')
    await waitFor(() => expect(title).toHaveValue('Water the plants'))
    await user.clear(title)
    await user.type(title, 'Water all the plants')
    await user.tab()

    // The patch is held until a scope is chosen — nothing saved yet.
    const dialog = await screen.findByRole('dialog', { name: 'Apply to recurring task' })
    expect(dialog).toBeInTheDocument()
    expect(mockUpdateTask).not.toHaveBeenCalled()

    await user.click(
      screen.getByRole('button', { name: 'This and all future occurrences' }),
    )

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ title: 'Water all the plants', edit_scope: 'future' }),
      ),
    )
  })

  it('skip button confirms then calls the skip endpoint', async () => {
    const user = userEvent.setup()
    mockSkipOccurrence.mockResolvedValue({ ...baseTask, id: 8, due_date: '2026-06-08' })
    renderDetail(baseTask)

    await user.click(await screen.findByRole('button', { name: /Skip this occurrence/ }))
    // A confirmation gate stands between the click and the request.
    expect(mockSkipOccurrence).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Skip occurrence' }))

    await waitFor(() => expect(mockSkipOccurrence).toHaveBeenCalledWith(7))
    // Skip never marks the occurrence done — it soft-deletes and rolls forward.
    expect(mockUpdateTask).not.toHaveBeenCalled()
  })

  it('lazily loads the series timeline and marks the current/skipped rows', async () => {
    const user = userEvent.setup()
    mockGetTaskSeries.mockResolvedValue({
      recurrence_id: 'series-abc',
      occurrences: [
        { ...baseTask, id: 5, due_date: '2026-05-18', workflow_status: 'done' },
        { ...baseTask, id: 6, due_date: '2026-05-25', deleted_at: '2026-05-25T00:00:00Z' },
        baseTask, // id 7 — the current occurrence
      ],
    })
    renderDetail(baseTask)

    // Not fetched until the section is expanded.
    await screen.findByRole('button', { name: 'Show occurrences' })
    expect(mockGetTaskSeries).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Show occurrences' }))

    await waitFor(() => expect(mockGetTaskSeries).toHaveBeenCalledWith(7))
    expect(await screen.findByText('Skipped')).toBeInTheDocument()
    expect(screen.getByText('This occurrence')).toBeInTheDocument()
  })

  it('stop recurrence confirms then calls the endpoint', async () => {
    const user = userEvent.setup()
    mockStopRecurrence.mockResolvedValue({ ...baseTask, repeat_interval: null })
    renderDetail(baseTask)

    await user.click(await screen.findByRole('button', { name: 'Stop recurrence' }))
    // A confirmation gate stands between the click and the request.
    expect(mockStopRecurrence).not.toHaveBeenCalled()
    await user.click(
      screen.getByRole('alertdialog', { name: 'Confirm stop recurrence' }).querySelector('button')!,
    )

    await waitFor(() => expect(mockStopRecurrence).toHaveBeenCalledWith(7))
  })
})
