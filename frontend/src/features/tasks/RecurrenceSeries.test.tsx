import { useState } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getTaskSeries, stopRecurrence } from '../../api/tasks'
import type { Task } from '../../types/task'
import { RecurrenceSeries } from './RecurrenceSeries'

vi.mock('../../api/tasks', () => ({
  getTaskSeries: vi.fn(),
  stopRecurrence: vi.fn(),
}))

const mockGetTaskSeries = vi.mocked(getTaskSeries)
const mockStopRecurrence = vi.mocked(stopRecurrence)

const baseTask: Task = {
  id: 7,
  project_id: 1,
  parent_task_id: null,
  title: 'Water the plants',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: '2026-06-01',
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: { unit: 'week', every: 1 },
  recurrence_id: 'series-abc',
  next_occurrence_date: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

/** The successor the backend spawns when the current occurrence is completed. */
const successor: Task = {
  ...baseTask,
  id: 8,
  title: 'Water the plants (next week)',
  due_date: '2026-06-08',
  updated_at: '2026-06-01T12:00:00Z',
}

function Panel({ task, onStopped }: { task: Task; onStopped?: (t: Task) => void }) {
  return (
    <MemoryRouter>
      <RecurrenceSeries task={task} onStopped={onStopped ?? (() => {})} onSkip={() => {}} />
    </MemoryRouter>
  )
}

/** Mirrors TaskDetailView's `applyUpdated`: the task is swapped in place and the
 *  panel stays mounted, because the route id never changed. */
function StatefulPanel({ initial }: { initial: Task }) {
  const [task, setTask] = useState(initial)
  return <Panel task={task} onStopped={setTask} />
}

async function expand(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Show occurrences' }))
}

describe('RecurrenceSeries invalidation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTaskSeries.mockResolvedValue({
      recurrence_id: 'series-abc',
      occurrences: [baseTask],
    })
  })

  afterEach(cleanup)

  it('is still lazy — nothing is fetched until the panel is expanded', async () => {
    render(<Panel task={baseTask} />)

    await screen.findByRole('button', { name: 'Show occurrences' })
    expect(mockGetTaskSeries).not.toHaveBeenCalled()
  })

  it('reflects a completion that happened while the panel was open', async () => {
    const user = userEvent.setup()
    mockGetTaskSeries.mockResolvedValueOnce({
      recurrence_id: 'series-abc',
      occurrences: [baseTask],
    })
    const { rerender } = render(<Panel task={baseTask} />)

    await expand(user)
    expect(await screen.findByText('Open')).toBeInTheDocument()
    // The successor does not exist yet.
    expect(screen.queryByRole('link')).toBeNull()

    // The backend completed the occurrence and spawned its successor.
    mockGetTaskSeries.mockResolvedValue({
      recurrence_id: 'series-abc',
      occurrences: [{ ...baseTask, workflow_status: 'done' }, successor],
    })
    const completed: Task = {
      ...baseTask,
      workflow_status: 'done',
      updated_at: '2026-06-01T12:00:00Z',
    }
    rerender(<Panel task={completed} />)

    // Without invalidation the timeline would still read "Open" and omit the
    // successor for as long as the user stays on this task. (issue #259)
    expect(await screen.findByText('Done')).toBeInTheDocument()
    expect(
      await screen.findByRole('link', { name: 'Water the plants (next week)' }),
    ).toBeInTheDocument()
    expect(mockGetTaskSeries).toHaveBeenCalledTimes(2)
  })

  it('refetches on every expand rather than serving the first load', async () => {
    const user = userEvent.setup()
    mockGetTaskSeries.mockResolvedValueOnce({
      recurrence_id: 'series-abc',
      occurrences: [baseTask],
    })
    render(<Panel task={baseTask} />)

    await expand(user)
    await waitFor(() => expect(mockGetTaskSeries).toHaveBeenCalledWith(7))
    expect(screen.queryByRole('link')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Hide occurrences' }))

    // The series moved on while the panel was collapsed.
    mockGetTaskSeries.mockResolvedValue({
      recurrence_id: 'series-abc',
      occurrences: [baseTask, successor],
    })
    await expand(user)

    expect(
      await screen.findByRole('link', { name: 'Water the plants (next week)' }),
    ).toBeInTheDocument()
    expect(mockGetTaskSeries).toHaveBeenCalledTimes(2)
  })

  it('refreshes the timeline after recurrence is stopped', async () => {
    const user = userEvent.setup()
    const stopped: Task = {
      ...baseTask,
      repeat_interval: null,
      updated_at: '2026-06-01T12:00:00Z',
    }
    mockStopRecurrence.mockResolvedValue(stopped)
    mockGetTaskSeries.mockResolvedValueOnce({
      recurrence_id: 'series-abc',
      occurrences: [baseTask, successor],
    })
    render(<StatefulPanel initial={baseTask} />)

    await expand(user)
    expect(
      await screen.findByRole('link', { name: 'Water the plants (next week)' }),
    ).toBeInTheDocument()

    // Stopping drops the not-yet-due successor from the series server-side.
    mockGetTaskSeries.mockResolvedValue({
      recurrence_id: 'series-abc',
      occurrences: [baseTask],
    })
    await user.click(screen.getByRole('button', { name: 'Stop recurrence' }))
    await user.click(
      screen.getByRole('alertdialog', { name: 'Confirm stop recurrence' }).querySelector('button')!,
    )

    await waitFor(() => expect(mockStopRecurrence).toHaveBeenCalledWith(7))
    await waitFor(() =>
      expect(screen.queryByRole('link', { name: 'Water the plants (next week)' })).toBeNull(),
    )
    expect(mockGetTaskSeries).toHaveBeenCalledTimes(2)
  })

  it('surfaces a refresh failure instead of leaving the stale list on screen', async () => {
    const user = userEvent.setup()
    mockGetTaskSeries.mockResolvedValueOnce({
      recurrence_id: 'series-abc',
      occurrences: [baseTask, successor],
    })
    const { rerender } = render(<Panel task={baseTask} />)

    await expand(user)
    await screen.findByRole('link', { name: 'Water the plants (next week)' })

    mockGetTaskSeries.mockRejectedValue(new Error('Series unavailable'))
    rerender(<Panel task={{ ...baseTask, updated_at: '2026-06-01T12:00:00Z' }} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Series unavailable')
    expect(screen.queryByRole('link', { name: 'Water the plants (next week)' })).toBeNull()
  })
})
