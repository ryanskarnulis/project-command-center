import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getTodayPlan } from '../../api/today'
import { markTaskDone, updateTask } from '../../api/tasks'
import type { ScheduledBlock, TodayPlan } from '../../types/today'
import { TodayPage } from './TodayPage'

vi.mock('../../api/today', () => ({
  getTodayPlan: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  markTaskDone: vi.fn(),
  updateTask: vi.fn(),
}))

const mockGetTodayPlan = vi.mocked(getTodayPlan)
const mockMarkTaskDone = vi.mocked(markTaskDone)
const mockUpdateTask = vi.mocked(updateTask)

function scheduledBlock(overrides: Partial<ScheduledBlock> = {}): ScheduledBlock {
  return {
    task_id: 7,
    title: 'Draft launch checklist',
    project_id: 1,
    start_time: '09:00',
    end_time: '09:30',
    estimated_minutes: 30,
    estimate_assumed: false,
    priority: 'high',
    workflow_status: 'open',
    due_date: null,
    due_signal: 'none',
    reason: 'open · high priority',
    ...overrides,
  }
}

function makePlan(overrides: Partial<TodayPlan> = {}): TodayPlan {
  return {
    date: '2026-06-20',
    start_time: '09:00',
    available_minutes: 360,
    used_minutes: 90,
    scheduled: [],
    overflow: [],
    blocked: [],
    ...overrides,
  }
}

describe('TodayPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders the scheduled timeline with task links and assumed estimates', async () => {
    mockGetTodayPlan.mockResolvedValue(
      makePlan({
        used_minutes: 60,
        scheduled: [
          {
            task_id: 7,
            title: 'Draft launch checklist',
            project_id: 1,
            start_time: '09:00',
            end_time: '09:30',
            estimated_minutes: 30,
            estimate_assumed: true,
            priority: 'high',
            workflow_status: 'in_progress',
            due_date: '2026-06-20',
            due_signal: 'due_today',
            reason: 'in-progress · due today · high priority',
          },
        ],
      }),
    )

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    const link = await screen.findByRole('link', { name: 'Draft launch checklist' })
    expect(link).toHaveAttribute('href', '/tasks/7')
    expect(screen.getByText('in-progress · due today · high priority')).toBeInTheDocument()
    expect(screen.getByText('assumed')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Timeline' })).toBeInTheDocument()
  })

  it('renders overflow and blocked sections with dependency warnings', async () => {
    mockGetTodayPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          {
            task_id: 1,
            title: 'Scheduled work',
            project_id: null,
            start_time: '09:00',
            end_time: '10:00',
            estimated_minutes: 60,
            estimate_assumed: false,
            priority: 'medium',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            reason: 'open · medium priority',
          },
        ],
        overflow: [
          {
            task_id: 2,
            title: 'Overflow task',
            project_id: null,
            priority: 'low',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            estimated_minutes: 45,
            estimate_assumed: false,
          },
        ],
        blocked: [
          {
            task_id: 3,
            title: 'Blocked task',
            project_id: null,
            priority: 'urgent',
            due_date: null,
            blocking_tasks: [
              { task_id: 9, title: 'Upstream dependency', workflow_status: 'in_progress' },
            ],
          },
        ],
      }),
    )

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: /Didn.t fit \(1\)/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Overflow task' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Blocked (1)' })).toBeInTheDocument()
    expect(screen.getByText(/Waiting on 1 unfinished dependency/)).toBeInTheDocument()
    // The blocker is named (not a bare #id) and shows its workflow status.
    const blockerLink = screen.getByRole('link', { name: 'Upstream dependency' })
    expect(blockerLink).toHaveAttribute('href', '/tasks/9')
    expect(screen.getByText('in progress')).toBeInTheDocument()
  })

  it('completes a scheduled task in-row and refetches the plan', async () => {
    mockGetTodayPlan.mockResolvedValue(
      makePlan({ scheduled: [scheduledBlock({ task_id: 7, workflow_status: 'open' })] }),
    )
    mockMarkTaskDone.mockResolvedValue({} as never)

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    const doneButton = await screen.findByRole('button', {
      name: 'Mark Draft launch checklist done',
    })
    // Open rows also expose Start (→ in_progress).
    expect(
      screen.getByRole('button', { name: 'Start Draft launch checklist' }),
    ).toBeInTheDocument()

    fireEvent.click(doneButton)

    // Recurrence-safe: goes through the dedicated done endpoint, not a PATCH.
    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(7))
    expect(mockUpdateTask).not.toHaveBeenCalled()
    // Initial load + post-mutation refetch.
    await waitFor(() => expect(mockGetTodayPlan).toHaveBeenCalledTimes(2))
  })

  it('starts an open task via the in-row Start action', async () => {
    mockGetTodayPlan.mockResolvedValue(
      makePlan({ scheduled: [scheduledBlock({ task_id: 7, workflow_status: 'open' })] }),
    )
    mockUpdateTask.mockResolvedValue({} as never)

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start Draft launch checklist' }),
    )

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(7, { workflow_status: 'in_progress' }),
    )
    await waitFor(() => expect(mockGetTodayPlan).toHaveBeenCalledTimes(2))
  })

  it('hides Start on an in-progress row but still offers Mark done', async () => {
    mockGetTodayPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          scheduledBlock({ task_id: 7, title: 'WIP task', workflow_status: 'in_progress' }),
        ],
      }),
    )

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    await screen.findByRole('link', { name: 'WIP task' })
    expect(
      screen.queryByRole('button', { name: 'Start WIP task' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Mark WIP task done' }),
    ).toBeInTheDocument()
  })

  it('shows an empty state when nothing is schedulable', async () => {
    mockGetTodayPlan.mockResolvedValue(makePlan({ used_minutes: 0 }))

    render(
      <MemoryRouter>
        <TodayPage />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText('No open tasks to schedule for this day.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Timeline' })).not.toBeInTheDocument()
  })
})
