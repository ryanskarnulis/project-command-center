import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getDashboard, getProjectSummary } from '../../api/dashboard'
import {
  createInbox,
  getInbox,
  listPendingInbox,
  processInbox,
} from '../../api/inbox'
import { listProjects } from '../../api/projects'
import { listAllTasks } from '../../api/tasks'
import type { DashboardOverview } from '../../types/dashboard'
import type { InboxItem } from '../../types/inbox'
import type { Task } from '../../types/task'
import { DashboardPage } from './DashboardPage'

vi.mock('../../api/dashboard', () => ({
  getDashboard: vi.fn(),
  getProjectSummary: vi.fn(),
}))

vi.mock('../../api/inbox', () => ({
  createInbox: vi.fn(),
  dismissInbox: vi.fn(),
  getCandidates: vi.fn(),
  getInbox: vi.fn(),
  listInbox: vi.fn(),
  listPendingInbox: vi.fn(),
  processInbox: vi.fn(),
  reviewInbox: vi.fn(),
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
const mockGetProjectSummary = vi.mocked(getProjectSummary)
const mockCreateInbox = vi.mocked(createInbox)
const mockGetInbox = vi.mocked(getInbox)
const mockListPendingInbox = vi.mocked(listPendingInbox)
const mockProcessInbox = vi.mocked(processInbox)
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
  recent_inbox: [
    {
      id: 10,
      source: 'web',
      summary: 'Review launch notes',
      processed_at: '2026-06-01T17:00:00Z',
      reviewed_at: null,
      resolved_project_id: 1,
      created_at: '2026-06-01T17:00:00Z',
    },
  ],
}

const tasks: Task[] = [
  {
    id: 1,
    project_id: 1,
    inbox_item_id: null,
    parent_task_id: null,
    title: 'Fix project dashboard',
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
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
    is_blocked: true,
  },
  {
    id: 2,
    project_id: 1,
    inbox_item_id: null,
    parent_task_id: null,
    title: 'Review capture queue',
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'medium',
    due_date: localDateOffset(1),
    estimated_minutes: null,
    repeat_interval: null,
    recurrence_id: null,
    confidence: null,
    assignee_hint: null,
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
    is_blocked: false,
  },
]

const pending: InboxItem[] = [
  {
    id: 100,
    raw_text: 'messy note',
    input_hash: 'hash',
    source: 'web',
    summary: 'messy note',
    project_hint: null,
    needs_review: true,
    processed_at: '2026-06-01T17:00:00Z',
    reviewed_at: null,
    model_name: 'test-model',
    suggested_project_id: null,
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
  },
]

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetDashboard.mockResolvedValue(overview)
    mockGetProjectSummary.mockResolvedValue({
      project_id: 1,
      summary: 'summary',
      model_name: 'test-model',
    })
    mockListAllTasks.mockResolvedValue(tasks)
    mockListPendingInbox.mockResolvedValue(pending)
    mockListProjects.mockResolvedValue([])
    mockCreateInbox.mockResolvedValue(pending[0])
    mockGetInbox.mockResolvedValue(pending[0])
    mockProcessInbox.mockResolvedValue([
      {
        id: 200,
        project_id: null,
        inbox_item_id: pending[0].id,
        parent_task_id: null,
        title: 'Turn notes into a task',
        description: null,
        review_status: 'candidate',
        workflow_status: 'open',
        priority: 'medium',
        due_date: null,
        estimated_minutes: null,
        repeat_interval: null,
        recurrence_id: null,
        confidence: 0.86,
        assignee_hint: null,
        created_at: '2026-06-01T17:00:00Z',
        updated_at: '2026-06-01T17:00:00Z',
        is_blocked: false,
      },
    ])
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
    expect(screen.getByRole('link', { name: 'Open Tasks: Add task' })).toBeInTheDocument()
    expect(screen.getByText('Awaiting Review')).toBeInTheDocument()
    expect(screen.getByText("Today's Tasks / Due Soon")).toBeInTheDocument()
    expect(screen.queryByText('Training Progress')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Capture Tasks' })).toBeInTheDocument()
    expect(
      screen.getByRole('textbox', { name: 'Messy text for AI task extraction' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Extract tasks' })).toBeInTheDocument()
    expect(screen.getByText('Customer Portal')).toBeInTheDocument()
    expect(screen.queryByText('Review launch notes')).not.toBeInTheDocument()
    // Pending captures surface as the "Awaiting Review" metric card linking to
    // /inbox (the inline dashboard pending-list was removed); the list itself
    // lives on the Inbox page now.
    expect(
      await screen.findByRole('link', { name: 'Awaiting Review: Review now' }),
    ).toBeInTheDocument()
    expect(screen.getByText('1 blocked task')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Create project' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Quick Actions' })).not.toBeInTheDocument()
  })

  it('extracts messy text and shows task candidates for approval on the dashboard', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    )

    const input = await screen.findByRole('textbox', {
      name: 'Messy text for AI task extraction',
    })
    await user.type(input, 'turn this messy thought into a task')
    await user.click(screen.getByRole('button', { name: 'Extract tasks' }))

    expect(mockCreateInbox).toHaveBeenCalledWith({
      raw_text: 'turn this messy thought into a task',
    })
    expect(mockProcessInbox).toHaveBeenCalledWith(pending[0].id)
    expect(
      await screen.findByRole('heading', { name: 'Review candidates (1)' }),
    ).toBeInTheDocument()
    expect(screen.getByDisplayValue('Turn notes into a task')).toBeInTheDocument()
  })

  it('keeps unavailable screenshot-inspired controls disabled', async () => {
    render(
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Focus Now' })

    expect(
      screen.getByRole('button', { name: 'Customize Command Center' }),
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Ask AI' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /View calendar/ })).toBeDisabled()
  })
})
