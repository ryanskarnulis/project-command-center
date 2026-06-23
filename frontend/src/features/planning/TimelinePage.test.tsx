import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectGantt, previewWhatIf } from '../../api/planning'
import { getProject } from '../../api/projects'
import { updateTask } from '../../api/tasks'
import { ToastProvider } from '../../components/ToastProvider'
import type { Task } from '../../types/task'
import { TimelinePage } from './TimelinePage'

vi.mock('../../api/planning', () => ({
  getProjectGantt: vi.fn(),
  previewWhatIf: vi.fn(),
}))
vi.mock('../../api/projects', () => ({ getProject: vi.fn() }))
vi.mock('../../api/tasks', () => ({ updateTask: vi.fn() }))

const mockGetGantt = vi.mocked(getProjectGantt)
const mockPreviewWhatIf = vi.mocked(previewWhatIf)
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

describe('TimelinePage unschedule', () => {
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

  afterEach(() => vi.restoreAllMocks())

  it('clicking the unschedule control clears both dates so the task buckets', async () => {
    // A remaining due_date would back-schedule into a bar, so unschedule clears it
    // too — otherwise the task never reaches the unscheduled bucket.
    // Initial load: one scheduled bar. The post-PATCH refetch returns it cleared,
    // so it lands in the unscheduled bucket.
    mockGetGantt
      .mockResolvedValueOnce({
        tasks: [
          task({
            id: 7,
            scheduled_start: '2026-06-20',
            due_date: '2026-06-25',
            estimated_minutes: 60,
          }),
        ],
        dependencies: [],
      })
      .mockResolvedValue({
        tasks: [task({ id: 7, scheduled_start: null, due_date: null })],
        dependencies: [],
      })
    mockUpdateTask.mockResolvedValue(
      task({ id: 7, scheduled_start: null, due_date: null }),
    )

    renderTimeline()
    const control = await screen.findByRole('button', { name: 'Unschedule task' })
    fireEvent.click(control)

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(7, {
        scheduled_start: null,
        due_date: null,
      })
    })
    expect(await screen.findByText('Task unscheduled')).toBeInTheDocument()
    // The bar is gone and the task now sits in the unscheduled bucket.
    await waitFor(() => {
      expect(document.querySelector('.gantt-bar')).toBeNull()
    })
    expect(document.querySelector('.gantt-unscheduled-item')).toHaveTextContent(
      'Task 7',
    )
  })
})

describe('TimelinePage what-if mode (Slice 6)', () => {
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
    mockGetGantt.mockResolvedValue({
      tasks: [task({ id: 7, scheduled_start: '2026-06-20', estimated_minutes: 60 })],
      dependencies: [],
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  async function enterWhatIf() {
    const toggle = await screen.findByRole('button', { name: 'What-if mode' })
    fireEvent.click(toggle)
  }

  it('staging a drag previews via the backend and does not PATCH', async () => {
    mockPreviewWhatIf.mockResolvedValue({
      shifts: [{ task_id: 7, scheduled_start: '2026-06-22' }],
    })

    const { container } = renderTimeline()
    await enterWhatIf()
    await dragBarRight(container, 2)

    await waitFor(() => {
      expect(mockPreviewWhatIf).toHaveBeenCalledWith(1, [
        { task_id: 7, scheduled_start: '2026-06-22' },
      ])
    })
    // What-if never persists until Apply.
    expect(mockUpdateTask).not.toHaveBeenCalled()
    expect(screen.getByText('1 staged change')).toBeInTheDocument()
  })

  it('Apply commits each staged change via the task PATCH, then exits', async () => {
    mockPreviewWhatIf.mockResolvedValue({
      shifts: [{ task_id: 7, scheduled_start: '2026-06-22' }],
    })
    mockUpdateTask.mockResolvedValue(task({ id: 7, scheduled_start: '2026-06-22' }))

    const { container } = renderTimeline()
    await enterWhatIf()
    await dragBarRight(container, 2)
    await screen.findByText('1 staged change')

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(7, {
        scheduled_start: '2026-06-22',
      })
    })
    expect(await screen.findByText('Schedule changes applied')).toBeInTheDocument()
    // Back out of what-if mode: the toggle returns.
    expect(
      await screen.findByRole('button', { name: 'What-if mode' }),
    ).toBeInTheDocument()
  })

  it('Discard leaves what-if mode without persisting anything', async () => {
    mockPreviewWhatIf.mockResolvedValue({
      shifts: [{ task_id: 7, scheduled_start: '2026-06-22' }],
    })

    const { container } = renderTimeline()
    await enterWhatIf()
    await dragBarRight(container, 2)
    await screen.findByText('1 staged change')

    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))

    expect(mockUpdateTask).not.toHaveBeenCalled()
    expect(
      await screen.findByRole('button', { name: 'What-if mode' }),
    ).toBeInTheDocument()
  })
})

describe('TimelinePage zoom levels', () => {
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

  afterEach(() => vi.restoreAllMocks())

  it('switches the grid bucketing from days to weeks and back', async () => {
    // Mon 2026-06-01 -> spans into a second week; 9 day columns, 2 week columns.
    mockGetGantt.mockResolvedValue({
      tasks: [
        task({ id: 7, scheduled_start: '2026-06-01', estimated_minutes: 480 * 9 }),
      ],
      dependencies: [],
    })

    const { container } = renderTimeline()
    const grid = await waitFor(() => {
      const el = container.querySelector('.gantt')
      if (!el) throw new Error('no grid yet')
      return el as HTMLElement
    })

    // Default day zoom: 9 day columns.
    expect(grid.classList.contains('gantt-zoom-day')).toBe(true)
    expect(container.querySelectorAll('.gantt-day-head')).toHaveLength(9)

    fireEvent.click(screen.getByRole('button', { name: 'Week' }))
    expect(grid.classList.contains('gantt-zoom-week')).toBe(true)
    expect(container.querySelectorAll('.gantt-day-head')).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Day' }))
    expect(grid.classList.contains('gantt-zoom-day')).toBe(true)
    expect(container.querySelectorAll('.gantt-day-head')).toHaveLength(9)
  })

  it('keeps drag day-resolution at week zoom (column spans 7 days)', async () => {
    mockGetGantt.mockResolvedValue({
      tasks: [task({ id: 7, scheduled_start: '2026-06-01', estimated_minutes: 60 })],
      dependencies: [],
    })
    mockUpdateTask.mockResolvedValue(task({ id: 7, scheduled_start: '2026-06-03' }))

    const { container } = renderTimeline()
    await waitFor(() => {
      if (!container.querySelector('.gantt-bar')) throw new Error('no bar yet')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Week' }))

    const bar = container.querySelector('.gantt-bar') as HTMLElement
    stubDayWidth() // one week column = DAY_WIDTH px wide
    // Dragging 2/7 of a column width should move the bar 2 days, not 2 weeks.
    fireEvent.pointerDown(bar, { button: 0, clientX: 100 })
    const dx = (2 / 7) * DAY_WIDTH
    fireEvent.pointerMove(window, { clientX: 100 + dx })
    fireEvent.pointerUp(window, { clientX: 100 + dx })

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(7, {
        scheduled_start: '2026-06-03',
      })
    })
  })
})

describe('TimelinePage drag from the unscheduled bucket (Slice 9)', () => {
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
    // One scheduled bar 06-20 -> 06-22 (3 day columns) gives the grid an axis;
    // one unscheduled task lands in the side bucket.
    mockGetGantt.mockResolvedValue({
      tasks: [
        task({ id: 7, scheduled_start: '2026-06-20', estimated_minutes: 480 * 3 }),
        task({ id: 9, title: 'No dates' }),
      ],
      dependencies: [],
    })
  })

  afterEach(() => vi.restoreAllMocks())

  /** Give each background column a distinct 50px-wide rect: col i = [i*50, i*50+50). */
  function stubColumnRects(): void {
    const cells = document.querySelectorAll('.gantt-col-bg')
    cells.forEach((cell, i) => {
      ;(cell as HTMLElement).getBoundingClientRect = () =>
        ({ left: i * 50, right: i * 50 + 50, width: 50, top: 0, bottom: 34, x: i * 50, y: 0, height: 34, toJSON: () => {} }) as DOMRect
    })
  }

  async function dragBucketItemTo(clientX: number) {
    const item = await waitFor(() => {
      const el = document.querySelector('.gantt-unscheduled-item')
      if (!el) throw new Error('no unscheduled item yet')
      return el as HTMLElement
    })
    stubColumnRects()
    fireEvent.pointerDown(item, { button: 0, clientX: 0, clientY: 0 })
    fireEvent.pointerMove(window, { clientX, clientY: 0 })
    fireEvent.pointerUp(window, { clientX, clientY: 0 })
  }

  it('PATCHes scheduled_start to the dropped column date', async () => {
    mockUpdateTask.mockResolvedValue(task({ id: 9, scheduled_start: '2026-06-21' }))

    renderTimeline()
    // Drop over column index 1 ([50,100)) -> 2026-06-21.
    await dragBucketItemTo(75)

    await waitFor(() => {
      expect(mockUpdateTask).toHaveBeenCalledWith(9, {
        scheduled_start: '2026-06-21',
      })
    })
    expect(await screen.findByText('Task rescheduled')).toBeInTheDocument()
  })

  it('does not PATCH when dropped off the grid columns', async () => {
    renderTimeline()
    await dragBucketItemTo(9999)

    await waitFor(() => {
      // settle: the bar exists so the page is rendered
      expect(document.querySelector('.gantt-unscheduled-item')).toBeTruthy()
    })
    expect(mockUpdateTask).not.toHaveBeenCalled()
  })

  it('stages via what-if instead of PATCHing when what-if mode is on', async () => {
    mockPreviewWhatIf.mockResolvedValue({
      shifts: [{ task_id: 9, scheduled_start: '2026-06-21' }],
    })

    renderTimeline()
    const toggle = await screen.findByRole('button', { name: 'What-if mode' })
    fireEvent.click(toggle)
    await dragBucketItemTo(75)

    await waitFor(() => {
      expect(mockPreviewWhatIf).toHaveBeenCalledWith(1, [
        { task_id: 9, scheduled_start: '2026-06-21' },
      ])
    })
    expect(mockUpdateTask).not.toHaveBeenCalled()
  })
})
