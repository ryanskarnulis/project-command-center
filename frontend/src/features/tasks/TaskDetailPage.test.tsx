import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
import { listDependencies } from '../../api/taskDependencies'
import { breakDownTask, getSubtasks, getTask, listAllTasks, reviewBreakdown, updateTask } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskDetailPage } from './TaskDetailPage'

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
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
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
    <MemoryRouter initialEntries={['/tasks/7']}>
      <Routes>
        <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TaskDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTask.mockResolvedValue(task)
    mockGetSubtasks.mockResolvedValue([])
    mockListAllTasks.mockResolvedValue([task])
    mockListProjects.mockResolvedValue([project])
    mockListDependencies.mockResolvedValue([])
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
    // The workflow status renders as a hero pill (the select also has an "Open"
    // option, so scope the assertion to the pill).
    expect(screen.getByText('Open', { selector: 'span.status-pill' })).toBeInTheDocument()
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

  it('saves workflow status changes inline', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.selectOptions(await screen.findByLabelText('Status'), 'in_progress')

    expect(mockUpdateTask).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ workflow_status: 'in_progress' }),
    )
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

  it('saves friendly estimate text inline', async () => {
    const user = userEvent.setup()
    renderDetail()

    const estimate = await screen.findByLabelText('Estimate')
    // Same draft-population race as the title field. The estimate loads empty
    // (no signal to wait on directly), but one effect populates every draft at
    // once — so the title showing its loaded value proves the estimate draft
    // has settled and won't clobber what we type.
    await waitFor(() =>
      expect(screen.getByLabelText('Task title')).toHaveValue('Patch the router'),
    )
    await user.type(estimate, '2h')
    await user.tab()

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ estimated_minutes: 120 }),
      ),
    )
  })
})
