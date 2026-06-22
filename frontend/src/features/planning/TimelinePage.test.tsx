import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectGantt } from '../../api/planning'
import { getProject } from '../../api/projects'
import { updateTask } from '../../api/tasks'
import { ToastProvider } from '../../components/ToastProvider'
import type { Task } from '../../types/task'
import { TimelinePage } from './TimelinePage'

vi.mock('../../api/planning', () => ({ getProjectGantt: vi.fn() }))
vi.mock('../../api/projects', () => ({ getProject: vi.fn() }))
vi.mock('../../api/tasks', () => ({ updateTask: vi.fn() }))

const mockGetGantt = vi.mocked(getProjectGantt)
const mockGetProject = vi.mocked(getProject)
const mockUpdateTask = vi.mocked(updateTask)

const DAY_WIDTH = 30

function task(overrides: Partial<Task> & { id: number }): Task {
  return {
    project_id: 1,
    inbox_item_id: null,
    parent_task_id: null,
    title: `Task ${overrides.id}`,
    description: null,
    review_status: 'accepted',
    workflow_status: 'open',
    priority: 'medium',
    due_date: null,
    scheduled_start: null,
    estimated_minutes: null,
    repeat_interval: null,
    recurrence_id: null,
    confidence: null,
    assignee_hint: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    is_blocked: false,
    is_blocking: false,
    blocked_task_count: 0,
    has_subtasks: false,
    ...overrides,
  }
}

/** jsdom has no layout, so getBoundingClientRect is all-zero — stub the day
 * column width the drag math measures. */
function stubDayWidth(): void {
  for (const cell of document.querySelectorAll('.gantt-col-bg')) {
    ;(cell as HTMLElement).getBoundingClientRect = () =>
      ({ width: DAY_WIDTH, height: 34, top: 0, left: 0, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => {} }) as DOMRect
  }
}

function renderTimeline() {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={['/projects/1/timeline']}>
        <Routes>
          <Route path="/projects/:projectId/timeline" element={<TimelinePage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  )
}

/** Drag the first bar `days` whole columns to the right and release. */
async function dragBarRight(container: HTMLElement, days: number) {
  const bar = await waitFor(() => {
    const el = container.querySelector('.gantt-bar')
    if (!el) throw new Error('no bar yet')
    return el as HTMLElement
  })
  stubDayWidth()
  fireEvent.pointerDown(bar, { button: 0, clientX: 100 })
  fireEvent.pointerMove(window, { clientX: 100 + days * DAY_WIDTH })
  fireEvent.pointerUp(window, { clientX: 100 + days * DAY_WIDTH })
  return bar
}

describe('TimelinePage drag-to-reschedule', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetProject.mockResolvedValue({
      id: 1,
      name: 'Launch',
      description: null,
      system_key: null,
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-01T00:00:00Z',
    } as Awaited<ReturnType<typeof getProject>>)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('PATCHes scheduled_start advanced by the dragged number of days', async () => {
    mockGetGantt.mockResolvedValue({
      tasks: [task({ id: 7, scheduled_start: '2026-06-20', estimated_minutes: 60 })],
      dependencies: [],
    })
    mockUpdateTask.mockResolvedValue(
      task({ id: 7, scheduled_start: '2026-06-22' }),
    )

    const { container } = renderTimeline()
    await dragBarRight(container, 2)

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(7, {
        scheduled_start: '2026-06-22',
      })
    })
    expect(await screen.findByText('Task rescheduled')).toBeInTheDocument()
  })

  it('reverts and shows an error toast when the PATCH fails', async () => {
    mockGetGantt.mockResolvedValue({
      tasks: [task({ id: 7, scheduled_start: '2026-06-20', estimated_minutes: 60 })],
      dependencies: [],
    })
    mockUpdateTask.mockRejectedValue(new Error('Network down'))

    const { container } = renderTimeline()
    await dragBarRight(container, 1)

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(7, {
        scheduled_start: '2026-06-21',
      })
    })
    expect(await screen.findByText('Network down')).toBeInTheDocument()
    // Failed reschedule never refetches the plan: only the initial load happened.
    expect(mockGetGantt).toHaveBeenCalledTimes(1)
  })

  it('does not PATCH for a click without movement (navigation, not a drag)', async () => {
    mockGetGantt.mockResolvedValue({
      tasks: [task({ id: 7, scheduled_start: '2026-06-20', estimated_minutes: 60 })],
      dependencies: [],
    })

    const { container } = renderTimeline()
    const bar = await waitFor(() => {
      const el = container.querySelector('.gantt-bar')
      if (!el) throw new Error('no bar yet')
      return el as HTMLElement
    })
    stubDayWidth()
    fireEvent.pointerDown(bar, { button: 0, clientX: 100 })
    fireEvent.pointerUp(window, { clientX: 100 })

    expect(mockUpdateTask).not.toHaveBeenCalled()
  })
})
