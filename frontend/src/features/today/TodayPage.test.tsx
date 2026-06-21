import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getTodayPlan } from '../../api/today'
import type { TodayPlan } from '../../types/today'
import { TodayPage } from './TodayPage'

vi.mock('../../api/today', () => ({
  getTodayPlan: vi.fn(),
}))

const mockGetTodayPlan = vi.mocked(getTodayPlan)

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
            blocking_task_ids: [9],
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
    expect(screen.getByRole('link', { name: '#9' })).toHaveAttribute('href', '/tasks/9')
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
