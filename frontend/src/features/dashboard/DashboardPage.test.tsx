import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getDashboard } from '../../api/dashboard'
import {
  listAllTasks,
  listCompletedTasks,
  markTaskDone,
  updateTask,
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
const mockUpdateTask = vi.mocked(updateTask)

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
  title: 'Fix login flow',
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
  title: 'Archive old invoices',
  workflow_status: 'done',
  due_date: null,
  is_blocking: false,
  blocked_task_count: 0,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

function lane(name: string): HTMLElement {
  const heading = screen.getByRole('link', { name })
  const section = heading.closest('section')
  if (!section) throw new Error(`no lane section for ${name}`)
  return section
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetDashboard.mockResolvedValue(overview)
    mockListAllTasks.mockResolvedValue(tasks)
    mockListCompletedTasks.mockResolvedValue([doneTask])
    mockMarkTaskDone.mockResolvedValue({ ...baseTask, workflow_status: 'done' })
    mockUpdateTask.mockResolvedValue(baseTask)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders one swimlane per project with cards in their status columns', async () => {
    renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Project board' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText('3 open tasks across all projects'),
    ).toBeInTheDocument()

    const portal = lane('Customer Portal')
    expect(within(portal).getByText('2 open tasks')).toBeInTheDocument()
    expect(within(portal).getByText('Blocking')).toBeInTheDocument()
    const openColumn = within(portal).getByRole('region', {
      name: 'Customer Portal Open',
    })
    expect(within(openColumn).getByText('Fix login flow')).toBeInTheDocument()
    const progressColumn = within(portal).getByRole('region', {
      name: 'Customer Portal In progress',
    })
    expect(
      within(progressColumn).getByText('Ship account settings'),
    ).toBeInTheDocument()

    const training = lane('Training Rollout')
    expect(
      within(training).getByText('Run facilitator session'),
    ).toBeInTheDocument()

    // The old dashboard surfaces are gone.
    expect(
      screen.queryByRole('heading', { name: 'Focus Now' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Projects Overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Workload')).not.toBeInTheDocument()
  })

  it('collapses quiet projects and expands them on demand', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Project board' })

    const quiet = lane('Quiet Project')
    expect(
      within(quiet).queryByRole('region', { name: 'Quiet Project Open' }),
    ).not.toBeInTheDocument()

    await userEvent.click(
      within(quiet).getByRole('button', { name: 'Expand Quiet Project' }),
    )
    expect(
      within(quiet).getByRole('region', { name: 'Quiet Project Open' }),
    ).toBeInTheDocument()
  })

  it('filters lanes through the signal strip and clears on second click', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Project board' })

    const overdue = screen.getByRole('button', { name: 'Overdue: 1 task' })
    expect(
      screen.getByRole('button', { name: 'Blocking: 1 task' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Due today: 1 task' }),
    ).toBeInTheDocument()

    await userEvent.click(overdue)
    // Only the overdue card remains; the lane without matches collapses.
    expect(screen.getByText('Fix login flow')).toBeInTheDocument()
    expect(screen.queryByText('Ship account settings')).not.toBeInTheDocument()
    expect(screen.queryByText('Run facilitator session')).not.toBeInTheDocument()

    await userEvent.click(overdue)
    expect(screen.getByText('Ship account settings')).toBeInTheDocument()
    expect(screen.getByText('Run facilitator session')).toBeInTheDocument()
  })

  it('lazily fetches a lane completed archive behind the Done toggle', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Project board' })
    expect(mockListCompletedTasks).not.toHaveBeenCalled()

    const portal = lane('Customer Portal')
    await userEvent.click(
      within(portal).getByRole('button', { name: 'Show done' }),
    )
    expect(mockListCompletedTasks).toHaveBeenCalledWith(1)
    expect(
      await within(portal).findByText('Archive old invoices'),
    ).toBeInTheDocument()
    expect(
      within(portal).getByRole('button', { name: 'Hide done (1)' }),
    ).toBeInTheDocument()
  })

  it('refiles a card dropped on another project lane', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Project board' })

    // Task 3 lives in Training Rollout (open); drop it on Customer Portal.
    const dataTransfer = { getData: () => '3', types: ['text/plain'] }
    const openColumn = within(lane('Customer Portal')).getByRole('region', {
      name: 'Customer Portal Open',
    })
    fireEvent.drop(openColumn, { dataTransfer })
    // Same column status, so only the project changes.
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(3, { project_id: 1 }),
    )

    const progressColumn = within(lane('Customer Portal')).getByRole('region', {
      name: 'Customer Portal In progress',
    })
    fireEvent.drop(progressColumn, { dataTransfer })
    // Different column: the card adopts the column status in the same PATCH.
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(3, {
        project_id: 1,
        workflow_status: 'in_progress',
      }),
    )
  })

  it('routes the complete circle through the recurrence-safe done endpoint', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Project board' })

    await userEvent.click(
      screen.getByRole('button', { name: 'Mark Fix login flow done' }),
    )
    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(1))
    expect(mockUpdateTask).not.toHaveBeenCalled()
  })
})
