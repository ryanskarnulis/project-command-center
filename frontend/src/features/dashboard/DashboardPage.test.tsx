import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getDashboard } from '../../api/dashboard'
import {
  listAllTasks,
  listCompletedTasks,
  markTaskDone,
} from '../../api/tasks'
import type { DashboardOverview } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { DashboardPage } from './DashboardPage'

vi.mock('../../api/dashboard', () => ({
  getDashboard: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  getTask: vi.fn(),
  listAllTasks: vi.fn(),
  listCompletedTasks: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  updateTask: vi.fn(),
}))

const mockGetDashboard = vi.mocked(getDashboard)
const mockListAllTasks = vi.mocked(listAllTasks)
const mockListCompletedTasks = vi.mocked(listCompletedTasks)
const mockMarkTaskDone = vi.mocked(markTaskDone)

function localDateOffset(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() + days)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const overview: DashboardOverview = {
  total_open_tasks: 3,
  projects: [
    { project_id: 1, project_name: 'Customer Portal', open_task_count: 2 },
    { project_id: 2, project_name: 'Training Rollout', open_task_count: 1 },
    { project_id: 3, project_name: 'Quiet Project', open_task_count: 0 },
  ],
}

const baseTask: Task = {
  id: 1,
  project_id: 1,
  parent_task_id: null,
  title: 'Fix project dashboard',
  description: null,
  review_status: 'accepted',
  workflow_status: 'open',
  priority: 'high',
  due_date: localDateOffset(-1),
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T17:00:00Z',
  updated_at: '2026-06-01T17:00:00Z',
  is_blocked: false,
  is_blocking: true,
  blocked_task_count: 2,
  has_subtasks: false,
}

const tasks: Task[] = [
  baseTask,
  {
    ...baseTask,
    id: 2,
    title: 'Ship account settings',
    workflow_status: 'in_progress',
    due_date: null,
    is_blocking: false,
    blocked_task_count: 0,
  },
  {
    ...baseTask,
    id: 3,
    project_id: 2,
    title: 'Run facilitator session',
    due_date: localDateOffset(0),
    is_blocking: false,
    blocked_task_count: 0,
  },
]

const doneTask: Task = {
  ...baseTask,
  id: 9,
  title: 'Publish portal brief',
  workflow_status: 'done',
  due_date: null,
  is_blocking: false,
  blocked_task_count: 0,
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetDashboard.mockResolvedValue(overview)
    mockListAllTasks.mockResolvedValue(tasks)
    mockListCompletedTasks.mockImplementation((projectId) =>
      Promise.resolve(projectId === 1 ? [doneTask] : []),
    )
    mockMarkTaskDone.mockResolvedValue({ ...baseTask, workflow_status: 'done' })
  })

  afterEach(cleanup)

  it('renders project swimlanes and removes the replaced dashboard surfaces', async () => {
    renderDashboard()

    expect(await screen.findByRole('heading', { name: 'Project board' })).toBeInTheDocument()
    const customerLane = screen.getByRole('region', { name: 'Customer Portal' })
    expect(within(customerLane).getByText('2 open tasks')).toBeInTheDocument()
    expect(within(customerLane).getByText('Fix project dashboard')).toBeInTheDocument()
    expect(within(customerLane).getByText('Ship account settings')).toBeInTheDocument()
    expect(within(customerLane).getByText('Blocking')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Add task' })).toHaveAttribute(
      'href',
      '/tasks?new=1',
    )

    const quietToggle = screen.getByRole('button', { name: 'Expand Quiet Project' })
    expect(quietToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: 'Quiet Project Open' })).not.toBeInTheDocument()

    expect(screen.queryByText('Focus Now')).not.toBeInTheDocument()
    expect(screen.queryByText('Projects Overview')).not.toBeInTheDocument()
    expect(screen.queryByText(/Good (morning|afternoon|evening)/)).not.toBeInTheDocument()
  })

  it('filters every lane from the clickable signal strip', async () => {
    const user = userEvent.setup()
    renderDashboard()
    await screen.findByRole('heading', { name: 'Project board' })

    const overdue = screen.getByRole('button', { name: 'Overdue: 1 task' })
    expect(screen.getByRole('button', { name: 'Blocking: 1 task' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Due today: 1 task' })).toBeInTheDocument()

    await user.click(overdue)

    expect(overdue).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Fix project dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Ship account settings')).not.toBeInTheDocument()
    expect(screen.queryByText('Run facilitator session')).not.toBeInTheDocument()

    await user.click(overdue)
    expect(screen.getByText('Ship account settings')).toBeInTheDocument()
  })

  it('fetches and reveals a project completed archive only on demand', async () => {
    const user = userEvent.setup()
    renderDashboard()
    await screen.findByRole('heading', { name: 'Project board' })

    expect(mockListCompletedTasks).not.toHaveBeenCalled()
    const customerLane = screen.getByRole('region', { name: 'Customer Portal' })
    await user.click(within(customerLane).getByRole('button', { name: 'Show done' }))

    expect(await within(customerLane).findByText('Publish portal brief')).toBeInTheDocument()
    expect(mockListCompletedTasks).toHaveBeenCalledWith(1)
    expect(within(customerLane).getByRole('button', { name: 'Hide done (1)' })).toBeInTheDocument()
  })

  it('uses the recurrence-safe done endpoint from a board card', async () => {
    const user = userEvent.setup()
    renderDashboard()
    await screen.findByRole('heading', { name: 'Project board' })

    await user.click(
      screen.getByRole('button', { name: 'Mark Fix project dashboard done' }),
    )

    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(1))
    await waitFor(() => expect(mockGetDashboard).toHaveBeenCalledTimes(2))
  })
})
