import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getDashboard } from '../../api/dashboard'
import { listProjects } from '../../api/projects'
import { listAllTasks } from '../../api/tasks'
import type { DashboardOverview } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { DashboardPage } from './DashboardPage'

vi.mock('../../api/dashboard', () => ({
  getDashboard: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  listAllTasks: vi.fn(),
  listCompletedTasks: vi.fn(() => Promise.resolve([])),
  reopenTask: vi.fn(),
}))

const mockGetDashboard = vi.mocked(getDashboard)
const mockListProjects = vi.mocked(listProjects)
const mockListAllTasks = vi.mocked(listAllTasks)

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
  ],
}

const tasks: Task[] = [
  {
    id: 1,
    project_id: 1,
    parent_task_id: null,
    title: 'Fix project dashboard',
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
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
    is_blocked: false,
    is_blocking: true,
    blocked_task_count: 2,
    has_subtasks: false,
  },
  {
    id: 2,
    project_id: 1,
    parent_task_id: null,
    title: 'Review capture queue',
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'medium',
    due_date: localDateOffset(1),
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
    is_blocking: false,
    blocked_task_count: 0,
    has_subtasks: false,
  },
]

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetDashboard.mockResolvedValue(overview)
    mockListAllTasks.mockResolvedValue(tasks)
    mockListProjects.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
  })

  it('renders command-center cards from existing API data', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Focus Now' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Tasks: View tasks' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add task' })).toBeInTheDocument()
    expect(screen.getByText('Focus / Due Soon')).toBeInTheDocument()
    expect(screen.queryByText('Training Progress')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Capture Tasks' })).not.toBeInTheDocument()
    expect(screen.getByText('Customer Portal')).toBeInTheDocument()
    const blockers = screen.getByRole('link', { name: 'Blocking Work: View blockers' })
    expect(blockers).toHaveAttribute('href', '/tasks?status=blocking')
    expect(screen.getByText('2 downstream tasks waiting')).toBeInTheDocument()
    expect(screen.getByText('Fix project dashboard')).toBeInTheDocument()
    expect(screen.getByText('2 tasks')).toBeInTheDocument()
    expect(screen.queryByText('1 blocked task')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create project' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Quick Actions' })).not.toBeInTheDocument()
  })

  it('does not ship inert placeholder controls', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Focus Now' })

    expect(
      screen.queryByRole('button', { name: 'Customize Command Center' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ask AI' })).not.toBeInTheDocument()
  })
})
