import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { getFocusPlan } from '../../api/focus'
import {
  getTask,
  markTaskDone,
  reopenTask,
  skipOccurrence,
  updateTask,
} from '../../api/tasks'
import type { ScheduledBlock, FocusPlan } from '../../types/focus'
import type { Task } from '../../types/task'
import { FocusPage } from './FocusPage'

vi.mock('../../api/focus', () => ({
  getFocusPlan: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getSubtasks: vi.fn(() => Promise.resolve([])),
  getTask: vi.fn(),
  getTaskSeries: vi.fn(),
  listAllTasks: vi.fn(() => Promise.resolve([])),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  skipOccurrence: vi.fn(),
  stopRecurrence: vi.fn(),
  updateTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(() => Promise.resolve([])),
}))

vi.mock('../../api/taskDependencies', () => ({
  addDependency: vi.fn(),
  listDependencies: vi.fn(() => Promise.resolve([])),
  listDependents: vi.fn(() => Promise.resolve([])),
  removeDependency: vi.fn(),
}))

const mockGetFocusPlan = vi.mocked(getFocusPlan)
const mockMarkTaskDone = vi.mocked(markTaskDone)
const mockReopenTask = vi.mocked(reopenTask)
const mockSkipOccurrence = vi.mocked(skipOccurrence)
const mockUpdateTask = vi.mocked(updateTask)
const mockGetTask = vi.mocked(getTask)

const panelTask: Task = {
  id: 7,
  project_id: 1,
  parent_task_id: null,
  title: 'Draft launch checklist',
  description: null,
  workflow_status: 'in_progress',
  priority: 'high',
  due_date: '2026-06-20',
  deferred_until: null,
  estimated_minutes: 30,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

function scheduledBlock(overrides: Partial<ScheduledBlock> = {}): ScheduledBlock {
  return {
    task_id: 7,
    title: 'Draft launch checklist',
    project_id: 1,
    start_time: '09:00',
    end_time: '09:30',
    start_day_offset: 0,
    end_day_offset: 0,
    estimated_minutes: 30,
    estimate_assumed: false,
    priority: 'high',
    workflow_status: 'open',
    due_date: null,
    due_signal: 'none',
    is_recurring: false,
    reason: 'open · high priority',
    parent_task_id: null,
    parent_title: null,
    ...overrides,
  }
}

function makePlan(overrides: Partial<FocusPlan> = {}): FocusPlan {
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

describe('FocusPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders the scheduled timeline with task links and assumed estimates', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        used_minutes: 60,
        scheduled: [
          {
            task_id: 7,
            title: 'Draft launch checklist',
            project_id: 1,
            start_time: '09:00',
            end_time: '09:30',
            start_day_offset: 0,
            end_day_offset: 0,
            estimated_minutes: 30,
            estimate_assumed: true,
            priority: 'high',
            workflow_status: 'in_progress',
            due_date: '2026-06-20',
            due_signal: 'due_today',
            is_recurring: false,
            reason: 'in-progress · due today · high priority',
            parent_task_id: null,
            parent_title: null,
          },
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    const link = await screen.findByRole('link', { name: 'Draft launch checklist' })
    // Row links open the peek panel in place via the ?task= param.
    expect(link).toHaveAttribute('href', '/?task=7')
    expect(screen.getByText('in-progress · due today · high priority')).toBeInTheDocument()
    expect(screen.getByText('assumed')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Timeline' })).toBeInTheDocument()
  })

  it('renders next-day block clocks with the derived calendar date', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          scheduledBlock({
            start_time: '23:00',
            end_time: '05:00',
            start_day_offset: 0,
            end_day_offset: 1,
            estimated_minutes: 360,
          }),
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    await screen.findByRole('link', { name: 'Draft launch checklist' })
    expect(screen.getByText('23:00')).toBeInTheDocument()
    expect(screen.getByText('Jun 21 · 05:00')).toBeInTheDocument()
    expect(screen.queryByText('29:00')).not.toBeInTheDocument()
  })

  it('validates a same-day end before requesting another plan', async () => {
    mockGetFocusPlan.mockResolvedValue(makePlan())

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    await waitFor(() => expect(mockGetFocusPlan).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Start time'), {
      target: { value: '18:00' },
    })
    await waitFor(() =>
      expect(mockGetFocusPlan).toHaveBeenLastCalledWith(
        expect.objectContaining({ startTime: '18:00' }),
      ),
    )
    mockGetFocusPlan.mockClear()

    fireEvent.change(screen.getByLabelText('Capacity'), {
      target: { value: 'until_end' },
    })

    expect(
      await screen.findByText('End of day must be later than start time.'),
    ).toHaveAttribute('role', 'alert')
    expect(screen.getByLabelText('End of day')).toHaveAttribute('aria-invalid', 'true')
    expect(mockGetFocusPlan).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('End of day'), {
      target: { value: '19:00' },
    })
    await waitFor(() =>
      expect(mockGetFocusPlan).toHaveBeenCalledWith({
        date: expect.any(String),
        startTime: '18:00',
        availableMinutes: 60,
      }),
    )
  })

  it('opens the peek panel over the plan when a row is clicked', async () => {
    mockGetFocusPlan.mockResolvedValue(makePlan({ scheduled: [scheduledBlock()] }))
    mockGetTask.mockResolvedValue(panelTask)

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    fireEvent.click(await screen.findByRole('link', { name: 'Draft launch checklist' }))

    expect(await screen.findByRole('dialog', { name: 'Task details' })).toBeInTheDocument()
    await waitFor(() => expect(mockGetTask).toHaveBeenCalledWith(7))
    // The plan is still rendered behind the panel.
    expect(screen.getByRole('heading', { name: 'Timeline' })).toBeInTheDocument()
  })

  it('renders overflow and blocked sections with dependency warnings', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          {
            task_id: 1,
            title: 'Scheduled work',
            project_id: 1,
            start_time: '09:00',
            end_time: '10:00',
            start_day_offset: 0,
            end_day_offset: 0,
            estimated_minutes: 60,
            estimate_assumed: false,
            priority: 'medium',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            is_recurring: false,
            reason: 'open · medium priority',
            parent_task_id: null,
            parent_title: null,
          },
        ],
        overflow: [
          {
            task_id: 2,
            title: 'Overflow task',
            project_id: 1,
            priority: 'low',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            is_recurring: false,
            estimated_minutes: 45,
            estimate_assumed: false,
            scheduled_subtask_count: 0,
          },
        ],
        blocked: [
          {
            task_id: 3,
            title: 'Blocked task',
            project_id: 1,
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
        <FocusPage />
      </MemoryRouter>,
    )

    // Secondary sections start collapsed: heading + count visible, rows hidden.
    const overflowToggle = (
      await screen.findByRole('heading', { name: /Didn.t fit \(1\)/ })
    ).closest('button')
    expect(overflowToggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('link', { name: 'Overflow task' })).not.toBeInTheDocument()

    fireEvent.click(overflowToggle!)
    expect(screen.getByRole('link', { name: 'Overflow task' })).toBeInTheDocument()

    const blockedToggle = screen
      .getByRole('heading', { name: 'Blocked (1)' })
      .closest('button')
    expect(
      screen.queryByText(/Waiting on 1 unfinished dependency/),
    ).not.toBeInTheDocument()
    fireEvent.click(blockedToggle!)
    expect(screen.getByText(/Waiting on 1 unfinished dependency/)).toBeInTheDocument()
    // The blocker is named (not a bare #id) and shows its workflow status.
    const blockerLink = screen.getByRole('link', { name: 'Upstream dependency' })
    expect(blockerLink).toHaveAttribute('href', '/?task=9')
    expect(screen.getByText('in progress')).toBeInTheDocument()
  })

  it('completes a scheduled task in-row and refetches the plan', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({ scheduled: [scheduledBlock({ task_id: 7, workflow_status: 'open' })] }),
    )
    mockMarkTaskDone.mockResolvedValue({} as never)

    render(
      <MemoryRouter>
        <FocusPage />
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
    await waitFor(() => expect(mockGetFocusPlan).toHaveBeenCalledTimes(2))
  })

  it('starts an open task via the in-row Start action', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({ scheduled: [scheduledBlock({ task_id: 7, workflow_status: 'open' })] }),
    )
    mockUpdateTask.mockResolvedValue({} as never)

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start Draft launch checklist' }),
    )

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(7, { workflow_status: 'in_progress' }),
    )
    await waitFor(() => expect(mockGetFocusPlan).toHaveBeenCalledTimes(2))
  })

  it('hides Start on an in-progress row but still offers Mark done', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          scheduledBlock({ task_id: 7, title: 'WIP task', workflow_status: 'in_progress' }),
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
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

  it('defers a task to the day after the plan date', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({ scheduled: [scheduledBlock({ task_id: 7, workflow_status: 'open' })] }),
    )
    mockUpdateTask.mockResolvedValue({} as never)

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Defer Draft launch checklist to tomorrow',
      }),
    )

    // Plan date is 2026-06-20, so the snooze lands on the 21st.
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(7, { deferred_until: '2026-06-21' }),
    )
    await waitFor(() => expect(mockGetFocusPlan).toHaveBeenCalledTimes(2))
  })

  it('swallows a rejected skip and does not refetch the plan', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          scheduledBlock({
            task_id: 7,
            workflow_status: 'open',
            is_recurring: true,
          }),
        ],
      }),
    )
    mockSkipOccurrence.mockRejectedValue(new Error('Skip failed'))
    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Skip this occurrence of Draft launch checklist',
      }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Skip occurrence' }),
    )

    await waitFor(() => expect(mockSkipOccurrence).toHaveBeenCalledWith(7))
    expect(mockGetFocusPlan).toHaveBeenCalledTimes(1)
  })

  it('labels a scheduled subtask with its parent task', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        scheduled: [
          scheduledBlock({
            task_id: 11,
            title: 'Write intro section',
            parent_task_id: 5,
            parent_title: 'Draft the whitepaper',
            reason: 'part of Draft the whitepaper · high priority',
          }),
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    await screen.findByRole('link', { name: 'Write intro section' })
    // The parent shows once, via the scheduler's reason string — not a second
    // dedicated "part of" line.
    expect(screen.getAllByText(/part of/)).toHaveLength(1)
    expect(
      screen.getByText('part of Draft the whitepaper · high priority'),
    ).toBeInTheDocument()
  })

  it('notes partially scheduled overflow tasks', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        used_minutes: 0,
        overflow: [
          {
            task_id: 2,
            title: 'Big parent',
            project_id: 1,
            priority: 'high',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            is_recurring: false,
            estimated_minutes: 720,
            estimate_assumed: false,
            scheduled_subtask_count: 2,
          },
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    const toggle = (
      await screen.findByRole('heading', { name: /Didn.t fit \(1\)/ })
    ).closest('button')
    fireEvent.click(toggle!)
    expect(screen.getByText('2 subtasks scheduled')).toBeInTheDocument()
  })

  it('shows an empty state when nothing is schedulable', async () => {
    mockGetFocusPlan.mockResolvedValue(makePlan({ used_minutes: 0 }))

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText('No open tasks to schedule for this day.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Timeline' })).not.toBeInTheDocument()
  })

  it('points at the overflow list when nothing fit but work overflowed', async () => {
    mockGetFocusPlan.mockResolvedValue(
      makePlan({
        used_minutes: 0,
        overflow: [
          {
            task_id: 2,
            title: 'Oversized task',
            project_id: 1,
            priority: 'high',
            workflow_status: 'open',
            due_date: null,
            due_signal: 'none',
            is_recurring: false,
            estimated_minutes: 720,
            estimate_assumed: false,
            scheduled_subtask_count: 0,
          },
        ],
      }),
    )

    render(
      <MemoryRouter>
        <FocusPage />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText('Nothing fit this session’s capacity — see ranked work below.'),
    ).toBeInTheDocument()
    expect(
      screen.queryByText('No open tasks to schedule for this day.'),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Didn.t fit \(1\)/ })).toBeInTheDocument()
  })

  // The "now" divider and the elapsed dimming are derived from the local clock,
  // so each case pins system time inside the plan's own day (2026-06-20).
  describe('now marker placement', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    function atLocalTime(hours: number, minutes: number): void {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      vi.setSystemTime(new Date(2026, 5, 20, hours, minutes, 0))
    }

    async function renderTimeline(): Promise<HTMLElement[]> {
      mockGetFocusPlan.mockResolvedValue(
        makePlan({
          scheduled: [
            scheduledBlock({ task_id: 1, title: 'Morning block', start_time: '09:00', end_time: '09:30' }),
            scheduledBlock({ task_id: 2, title: 'Afternoon block', start_time: '13:00', end_time: '13:30' }),
          ],
        }),
      )
      render(
        <MemoryRouter>
          <FocusPage />
        </MemoryRouter>,
      )
      await screen.findByRole('link', { name: 'Morning block' })
      const timeline = document.querySelector('.focus-timeline')
      if (!timeline) throw new Error('timeline not rendered')
      return Array.from(timeline.children) as HTMLElement[]
    }

    /** Row shape as ['marker' | 'block:<past?>'] in document order. */
    function shape(rows: HTMLElement[]): string[] {
      return rows.map((row) => {
        if (row.classList.contains('focus-now-marker')) return 'marker'
        return row.classList.contains('focus-block-past') ? 'block:past' : 'block:current'
      })
    }

    it('puts the marker above every block before the first one starts', async () => {
      atLocalTime(8, 0)
      expect(shape(await renderTimeline())).toEqual([
        'marker',
        'block:current',
        'block:current',
      ])
    })

    it('puts the marker above the block currently running', async () => {
      atLocalTime(9, 15)
      expect(shape(await renderTimeline())).toEqual([
        'marker',
        'block:current',
        'block:current',
      ])
    })

    it('puts the marker in the gap between two blocks', async () => {
      atLocalTime(11, 0)
      expect(shape(await renderTimeline())).toEqual([
        'block:past',
        'marker',
        'block:current',
      ])
    })

    it('marks every block elapsed and terminates the timeline once the day is over', async () => {
      atLocalTime(18, 0)
      expect(shape(await renderTimeline())).toEqual([
        'block:past',
        'block:past',
        'marker',
      ])
      expect(screen.getByLabelText('Now, 18:00')).toBeInTheDocument()
    })

    it('renders no marker or dimming when viewing another day', async () => {
      atLocalTime(18, 0)
      await renderTimeline()
      // Switching the Day control off today disables the clock overlay entirely.
      fireEvent.change(screen.getByLabelText('Day'), { target: { value: '2026-06-21' } })
      await waitFor(() =>
        expect(document.querySelector('.focus-now-marker')).not.toBeInTheDocument(),
      )
      const rows = Array.from(
        document.querySelectorAll('.focus-timeline > li'),
      ) as HTMLElement[]
      expect(shape(rows)).toEqual(['block:current', 'block:current'])
    })
  })
  /* M05i — the phone tree. Only the swipe's pointer sequence and the sheet's
     native <dialog> lifecycle are stubbed; both are verified for real in
     Chromium via the browser verifier. */
  describe('mobile handoff (M05i)', () => {
    const originalShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
    const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close')

    beforeEach(() => {
      // The session's start time defaults to "now", and the day to today — pin
      // both so the gutter's clock times and the defer target are assertable.
      vi.useFakeTimers({ shouldAdvanceTime: true })
      vi.setSystemTime(new Date(2026, 5, 20, 9, 0, 0))
      vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
      Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true } })
      Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false } })
    })

    afterEach(() => {
      cleanup()
      vi.useRealTimers()
      vi.unstubAllGlobals()
      if (originalShowModal) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', originalShowModal)
      else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
      if (originalClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose)
      else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
    })

    const dnsBlock = scheduledBlock({
      task_id: 1,
      title: 'Cut over DNS to the new resolver',
      estimated_minutes: 120,
      priority: 'urgent',
      end_time: '11:00',
    })
    const runbookBlock = scheduledBlock({
      task_id: 2,
      title: 'Review the on-call runbook',
      estimated_minutes: 30,
      priority: 'medium',
      workflow_status: 'in_progress',
      start_time: '11:00',
      end_time: '11:30',
    })

    function renderMobile(plan: FocusPlan) {
      mockGetFocusPlan.mockResolvedValue(plan)
      render(
        <MemoryRouter>
          <FocusPage />
        </MemoryRouter>,
      )
    }

    /** The gesture: only the content column translates, so that is what receives the pointer. */
    function swipe(index: number, dx: number) {
      const surface = document.querySelectorAll('.focus-swipe-content')[index]
      fireEvent.pointerDown(surface, { clientX: 0, button: 0, pointerId: 1 })
      fireEvent.pointerMove(surface, { clientX: dx, pointerId: 1 })
      fireEvent.pointerUp(surface, { clientX: dx, pointerId: 1 })
    }

    it('renders the band, the capacity bar and the free tail instead of the desktop controls', async () => {
      renderMobile(makePlan({ used_minutes: 150, scheduled: [dnsBlock, runbookBlock] }))

      expect(await screen.findByRole('link', { name: 'Cut over DNS to the new resolver' })).toBeInTheDocument()
      // The desktop control block and its four-fact summary are gone.
      expect(screen.queryByLabelText('Day')).toBeNull()
      expect(screen.queryByRole('heading', { name: 'Timeline' })).toBeNull()
      expect(screen.getByText('Now')).toBeInTheDocument()
      // One time per hairline: a row prints its start, the tail prints both.
      expect(screen.getByText('09:00')).toBeInTheDocument()
      expect(screen.getByText('11:00')).toBeInTheDocument()
      expect(screen.getByText('Ends 11:30')).toBeInTheDocument()
      // Twice by design: the caption states it, the dashed tail occupies it.
      expect(screen.getAllByText('3h 30m free')).toHaveLength(2)
      // Two meta items, hard cap: the loudest signal, then the context string.
      expect(screen.getByText('Urgent')).toBeInTheDocument()
      expect(screen.getByText('In progress')).toBeInTheDocument()
      expect(screen.getByText('Today · 09:00 → 15:00 · 6h capacity')).toBeInTheDocument()
    })

    it('makes the remaining-time readout the start/pause control', async () => {
      renderMobile(makePlan({ scheduled: [dnsBlock] }))

      const idle = await screen.findByRole('button', { name: /^Start Cut over DNS/ })
      expect(idle).toHaveTextContent('2h planned')
      fireEvent.click(idle)

      // The label is the state; the accessible name carries the verb, because
      // the chip reads as a status readout on its own.
      const running = screen.getByRole('button', { name: /^Pause Cut over DNS/ })
      expect(running).toHaveTextContent('2h left')
      fireEvent.click(running)
      expect(screen.getByRole('button', { name: /^Resume Cut over DNS/ })).toHaveTextContent('Paused · 2h left')
    })

    it('marks a block done on a right swipe and undoes it from the bar', async () => {
      mockMarkTaskDone.mockResolvedValue({ ...panelTask, id: 1 })
      mockReopenTask.mockResolvedValue({ ...panelTask, id: 1 })
      renderMobile(makePlan({ scheduled: [dnsBlock, runbookBlock] }))
      await screen.findByText('Now')

      swipe(0, 100)
      await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(1))
      expect(await screen.findByText('Marked done · Cut over DNS to the new resolver')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
      await waitFor(() => expect(mockReopenTask).toHaveBeenCalledWith(1))
      expect(screen.queryByText('Marked done · Cut over DNS to the new resolver')).toBeNull()
    })

    it('defers on a left swipe and snaps back below the threshold', async () => {
      mockUpdateTask.mockResolvedValue({ ...panelTask, id: 1 })
      renderMobile(makePlan({ scheduled: [dnsBlock] }))
      await screen.findByText('Now')

      swipe(0, -40)
      expect(mockUpdateTask).not.toHaveBeenCalled()

      swipe(0, -100)
      await waitFor(() =>
        expect(mockUpdateTask).toHaveBeenCalledWith(1, { deferred_until: '2026-06-21' }),
      )
      expect(await screen.findByText('Deferred · Cut over DNS to the new resolver')).toBeInTheDocument()
    })

    it('keeps every gesture verb reachable from the row sheet', async () => {
      mockUpdateTask.mockResolvedValue({ ...panelTask, id: 2 })
      renderMobile(makePlan({ scheduled: [dnsBlock, scheduledBlock({ task_id: 2, title: 'Weekly backup check', is_recurring: true })] }))
      await screen.findByText('Now')

      fireEvent.click(screen.getByRole('button', { name: 'Actions for Weekly backup check' }))
      const sheet = screen.getByRole('dialog')
      // No state verb on a row that isn't the current block.
      expect(within(sheet).queryByRole('button', { name: 'Start' })).toBeNull()
      expect(within(sheet).getByRole('button', { name: 'Mark done' })).toBeInTheDocument()
      expect(within(sheet).getByRole('button', { name: 'Skip occurrence' })).toBeInTheDocument()

      fireEvent.click(within(sheet).getByRole('button', { name: 'Defer to tomorrow' }))
      await waitFor(() =>
        expect(mockUpdateTask).toHaveBeenCalledWith(2, { deferred_until: '2026-06-21' }),
      )
    })

    it('schedules from Didn\u2019t fit by extending the session to hold it', async () => {
      renderMobile(
        makePlan({
          used_minutes: 120,
          scheduled: [dnsBlock],
          overflow: [
            {
              task_id: 8,
              title: 'Patch the Proxmox hosts',
              project_id: 1,
              priority: 'medium',
              workflow_status: 'open',
              due_date: null,
              due_signal: 'none',
              is_recurring: false,
              estimated_minutes: 45,
              estimate_assumed: false,
              scheduled_subtask_count: 0,
            },
          ],
        }),
      )
      await screen.findByText('Now')

      fireEvent.click(screen.getByRole('button', { name: /Pull from Didn/ }))
      // 120 scheduled + a 45m item that didn't fit; nothing finished yet.
      await waitFor(() => expect(localStorage.getItem('focus.capacity')).toBe('165'))
    })
    /* Regression, found in Chromium: a captured pointer retargets its own
       `pointerup` and the click derived from it to the capturing element, so
       capturing on press swallowed every tap on the row's ⋯, the clock chip and
       the title link. jsdom implements neither capture nor that retargeting, so
       the invariant is asserted directly: nothing is captured until the press
       has become a drag. */
    it('does not capture the pointer until the press is a drag', async () => {
      const capture = vi.fn()
      const original = Object.getOwnPropertyDescriptor(Element.prototype, 'setPointerCapture')
      Object.defineProperty(Element.prototype, 'setPointerCapture', { configurable: true, value: capture })
      try {
        renderMobile(makePlan({ scheduled: [dnsBlock] }))
        await screen.findByText('Now')
        const surface = document.querySelectorAll('.focus-swipe-content')[0]

        fireEvent.pointerDown(surface, { clientX: 0, button: 0, pointerId: 1 })
        expect(capture).not.toHaveBeenCalled()
        // Inside the slop the row has not moved, so the tap still belongs to
        // whatever it was aimed at.
        fireEvent.pointerMove(surface, { clientX: 5, pointerId: 1 })
        expect(capture).not.toHaveBeenCalled()

        fireEvent.pointerMove(surface, { clientX: 30, pointerId: 1 })
        expect(capture).toHaveBeenCalledWith(1)
        fireEvent.pointerUp(surface, { clientX: 30, pointerId: 1 })
      } finally {
        if (original) Object.defineProperty(Element.prototype, 'setPointerCapture', original)
        else Reflect.deleteProperty(Element.prototype, 'setPointerCapture')
      }
    })

    it('keeps the running clock when a later row is completed from its sheet', async () => {
      mockMarkTaskDone.mockResolvedValue({ ...panelTask, id: 2 })
      renderMobile(makePlan({ scheduled: [dnsBlock, runbookBlock] }))
      await screen.findByText('Now')

      fireEvent.click(screen.getByRole('button', { name: /^Start Cut over DNS/ }))
      expect(screen.getByRole('button', { name: /^Pause Cut over DNS/ })).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Actions for Review the on-call runbook' }))
      fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Mark done' }))
      await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(2))

      // Only the block that owns the clock ends it.
      expect(screen.getByRole('button', { name: /^Pause Cut over DNS/ })).toBeInTheDocument()
    })

    it('reports the backend\u2019s reason for a refused write, not its status code', async () => {
      mockUpdateTask.mockRejectedValue(
        new ApiError(409, { detail: 'A parent is completed by its subtasks.' }),
      )
      renderMobile(makePlan({ scheduled: [dnsBlock] }))
      await screen.findByText('Now')

      swipe(0, -100)
      expect(await screen.findByRole('alert')).toHaveTextContent('A parent is completed by its subtasks.')
    })
    /* Both found in Chromium after the capture fix. `setPointerCapture` throws
       NotFoundError when the pointer is already gone; letting that escape the
       move handler killed the drag before it could translate. And a keyboard
       click carries no preceding pointerdown, so the `dragged` flag a swipe
       leaves behind was suppressing it — the accessible path, broken by the
       accelerator it was meant to back up. */
    it('keeps dragging when the pointer cannot be captured', async () => {
      const original = Object.getOwnPropertyDescriptor(Element.prototype, 'setPointerCapture')
      Object.defineProperty(Element.prototype, 'setPointerCapture', {
        configurable: true,
        value: () => { throw new DOMException('No active pointer', 'NotFoundError') },
      })
      try {
        mockMarkTaskDone.mockResolvedValue({ ...panelTask, id: 1 })
        renderMobile(makePlan({ scheduled: [dnsBlock] }))
        await screen.findByText('Now')

        swipe(0, 100)
        await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(1))
      } finally {
        if (original) Object.defineProperty(Element.prototype, 'setPointerCapture', original)
        else Reflect.deleteProperty(Element.prototype, 'setPointerCapture')
      }
    })

    it('still opens a row sheet from the keyboard after the row has been dragged', async () => {
      renderMobile(makePlan({ scheduled: [dnsBlock] }))
      await screen.findByText('Now')

      // A drag that commits nothing, but leaves the row flagged as dragged.
      swipe(0, -40)
      const more = screen.getByRole('button', { name: 'Actions for Cut over DNS to the new resolver' })
      // Enter/Space arrive as a click with no pointer behind them: detail 0.
      fireEvent.click(more, { detail: 0 })

      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })
    // Anchors are draggable by default, and the browser's drag-and-drop takes
    // the pointer stream with it — a swipe that began on the title stalled.
    it('keeps the row titles out of the browser\u2019s native drag-and-drop', async () => {
      renderMobile(makePlan({ scheduled: [dnsBlock, runbookBlock] }))
      await screen.findByText('Now')

      expect(screen.getByRole('link', { name: 'Cut over DNS to the new resolver' })).toHaveAttribute('draggable', 'false')
      expect(screen.getByRole('link', { name: 'Review the on-call runbook' })).toHaveAttribute('draggable', 'false')
    })
  })
})
