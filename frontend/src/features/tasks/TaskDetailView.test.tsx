import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
import { listDependencies, listDependents } from '../../api/taskDependencies'
import { breakDownTask, getSubtasks, getTask, listAllTasks, reviewBreakdown, updateTask } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { todayISO } from '../../utils/dates'
import { TaskDetailView } from './TaskDetailView'

vi.mock('../../api/tasks', () => ({
  breakDownTask: vi.fn(),
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getSubtasks: vi.fn(),
  getTask: vi.fn(),
  listAllTasks: vi.fn(),
  reviewBreakdown: vi.fn(),
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

const task: Task = {
  id: 7,
  project_id: 1,
  inbox_item_id: null,
  parent_task_id: null,
  title: 'Patch the router',
  description: null,
  review_status: 'accepted',
  workflow_status: 'open',
  priority: 'high',
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

const project: Project = {
  id: 1,
  name: 'Infra',
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
const mockBreakDownTask = vi.mocked(breakDownTask)
const mockReviewBreakdown = vi.mocked(reviewBreakdown)

const suggestedSubtask: Task = {
  ...task,
  id: 21,
  parent_task_id: 7,
  title: 'Back up the config first',
  review_status: 'candidate',
}

function renderDetail() {
  return render(
    <MemoryRouter>
      <TaskDetailView taskId={7} />
    </MemoryRouter>,
  )
}

describe('TaskDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTask.mockResolvedValue(task)
    mockGetSubtasks.mockResolvedValue([])
    mockListAllTasks.mockResolvedValue([task])
    mockListProjects.mockResolvedValue([project])
    mockListDependencies.mockResolvedValue([])
    mockListDependents.mockResolvedValue([])
    mockUpdateTask.mockImplementation(async (_id, patch) => ({ ...task, ...patch }))
    mockBreakDownTask.mockResolvedValue([suggestedSubtask])
    mockReviewBreakdown.mockResolvedValue({
      approved: 1,
      dismissed: 0,
      finalized: true,
      training_example_id: 5,
    })
  })

  afterEach(cleanup)

  it('renders inline fields without the old edit button or review status', async () => {
    renderDetail()

    const title = await screen.findByLabelText('Task title')
    await waitFor(() => expect(title).toHaveValue('Patch the router'))
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByText('accepted')).not.toBeInTheDocument()
    // The workflow status renders as the status chip in the hero.
    expect(screen.getByRole('button', { name: 'Status: Open' })).toBeInTheDocument()
  })

  it('saves title changes inline on blur', async () => {
    const user = userEvent.setup()
    renderDetail()

    const title = await screen.findByLabelText('Task title')
    // The input mounts empty and is populated from the task by an effect; wait
    // for that draft to settle before editing, or the effect can clobber our
    // typed value mid-interaction and the blur sees no change.
    await waitFor(() => expect(title).toHaveValue('Patch the router'))
    await user.clear(title)
    await user.type(title, 'Patch the edge router')
    await user.tab()

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ title: 'Patch the edge router' }),
      ),
    )
  })

  it('guards refresh/close only while a field holds an unsaved edit', async () => {
    const user = userEvent.setup()
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    renderDetail()

    const title = await screen.findByLabelText('Task title')
    await waitFor(() => expect(title).toHaveValue('Patch the router'))
    expect(addSpy).not.toHaveBeenCalledWith('beforeunload', expect.any(Function))

    // Type without blurring: the edit is unsaved, so the guard attaches.
    await user.type(title, ' now')
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function)),
    )

    cleanup()
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  })

  it('saves workflow status changes from the status chip', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Status: Open' }))
    await user.click(screen.getByRole('button', { name: 'In progress' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ workflow_status: 'in_progress' }),
    )
  })

  it('saves a priority pick from the priority chip', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Priority: high' }))
    await user.click(screen.getByRole('button', { name: 'urgent' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ priority: 'urgent' }),
    )
  })

  it('saves a due date from the Today preset', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Set due date' }))
    await user.click(screen.getByRole('button', { name: 'Today' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ due_date: todayISO() }),
    )
  })

  it('saves a project pick from the project chip', async () => {
    const user = userEvent.setup()
    mockListProjects.mockResolvedValue([project, { ...project, id: 2, name: 'Garden' }])
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Project: Infra' }))
    await user.click(screen.getByRole('button', { name: 'Garden' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ project_id: 2 }),
    )
  })

  it('prompts for edit scope when a chip edits a recurring task', async () => {
    const user = userEvent.setup()
    mockGetTask.mockResolvedValue({
      ...task,
      recurrence_id: 'abc123',
      next_occurrence_date: null,
      due_date: '2026-07-10',
      repeat_interval: { unit: 'week', every: 1 },
    })
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Priority: high' }))
    await user.click(screen.getByRole('button', { name: 'urgent' }))

    // The scopable edit parks until a scope is chosen — no PATCH yet.
    expect(mockUpdateTask).not.toHaveBeenCalled()
    await user.click(
      screen.getByRole('button', { name: 'This and all future occurrences' }),
    )

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ priority: 'urgent', edit_scope: 'future' }),
      ),
    )
  })

  it('disables the status and estimate chips when values roll up from subtasks', async () => {
    mockGetTask.mockResolvedValue({
      ...task,
      has_subtasks: true,
      estimated_minutes: 90,
      workflow_status: 'in_progress',
    })
    renderDetail()

    const status = await screen.findByRole('button', { name: 'Status: In progress' })
    expect(status).toBeDisabled()
    expect(status).toHaveAttribute('title', 'Rolled up from subtasks')
    const estimate = screen.getByRole('button', { name: 'Estimate: 90 minutes' })
    expect(estimate).toBeDisabled()
    expect(estimate).toHaveAttribute('title', 'Sum of subtask estimates')
  })

  it('breaks a task down and approves a suggested subtask', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Break this down' }))

    await waitFor(() => expect(mockBreakDownTask).toHaveBeenCalledWith(7))
    // The suggested subtask renders as a card (scoped by its link aria-label —
    // the title also appears in the Parent-task dropdown).
    expect(
      await screen.findByRole('link', { name: 'Back up the config first' }),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(mockReviewBreakdown).toHaveBeenCalledWith(7, [
        { task_id: 21, action: 'approve' },
      ]),
    )
  })

  it('shows dependents when the task is blocking downstream work', async () => {
    mockGetTask.mockResolvedValue({
      ...task,
      is_blocking: true,
      blocked_task_count: 1,
    })
    mockListDependents.mockResolvedValue([
      {
        id: 99,
        task_id: 7,
        dependent_task_id: 12,
        dependent_title: 'Install the router',
        dependent_workflow_status: 'open',
        dependent_done: false,
      },
    ])

    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Blocking' })).toBeInTheDocument()
    expect(screen.getByText('1 downstream task waiting')).toBeInTheDocument()
    const dependent = await screen.findByRole('link', { name: 'Install the router' })
    expect(dependent).toHaveAttribute('href', '/tasks/12')
    expect(screen.getByText('waiting')).toBeInTheDocument()
  })

  it('saves friendly estimate text from the estimate chip', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Set estimate' }))
    await user.type(screen.getByLabelText('Estimate'), '2h')
    await user.click(screen.getByRole('button', { name: 'Set' }))

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ estimated_minutes: 120 }),
      ),
    )
  })
})
