import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getCalendar } from '../../api/calendar'
import type { Task } from '../../types/task'
import { CalendarPage } from './CalendarPage'

vi.mock('../../api/calendar', () => ({
  getCalendar: vi.fn(),
}))

const mockGetCalendar = vi.mocked(getCalendar)

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 1,
    project_id: null,
    inbox_item_id: null,
    parent_task_id: null,
    title: 'Ship the calendar',
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'high',
    due_date: '2026-06-15',
    estimated_minutes: null,
    repeat_interval: null,
    recurrence_id: null,
    confidence: null,
    assignee_hint: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    deleted_at: null,
    is_blocked: false,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CalendarPage />
    </MemoryRouter>,
  )
}

describe('CalendarPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Anchor the calendar on a fixed month so the grid range is deterministic.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date(2026, 5, 21)) // 2026-06-21 (local)
  })

  afterEach(() => {
    vi.useRealTimers()
    cleanup()
  })

  it('renders a task chip on its due-date cell linking to the task detail', async () => {
    mockGetCalendar.mockResolvedValue([makeTask({ id: 42, title: 'Ship it' })])

    renderPage()

    const chip = await screen.findByText('Ship it')
    const link = chip.closest('a')
    expect(link).toHaveAttribute('href', '/tasks/42')
    // Month heading reflects the anchor month.
    expect(screen.getByText('June 2026')).toBeInTheDocument()
  })

  it('requests a range covering the anchored month', async () => {
    mockGetCalendar.mockResolvedValue([])

    renderPage()

    await waitFor(() => expect(mockGetCalendar).toHaveBeenCalled())
    const { start, end } = mockGetCalendar.mock.calls[0][0]
    // June 1 2026 is a Monday → grid starts on the preceding Sunday, May 31.
    expect(start).toBe('2026-05-31')
    // June 30 2026 is a Tuesday → grid ends on the following Saturday, July 4.
    expect(end).toBe('2026-07-04')
  })

  it('moves the range when navigating to the previous month', async () => {
    mockGetCalendar.mockResolvedValue([])

    renderPage()
    await waitFor(() => expect(mockGetCalendar).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByLabelText('Previous'))

    await waitFor(() => expect(screen.getByText('May 2026')).toBeInTheDocument())
    await waitFor(() => expect(mockGetCalendar).toHaveBeenCalledTimes(2))
  })

  it('shows the empty state when nothing is due in range', async () => {
    mockGetCalendar.mockResolvedValue([])

    renderPage()

    expect(
      await screen.findByText('No tasks due in this range.'),
    ).toBeInTheDocument()
  })
})
