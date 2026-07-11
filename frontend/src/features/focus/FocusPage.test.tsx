import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getFocusPlan } from '../../api/focus'
import { getTask, markTaskDone, updateTask } from '../../api/tasks'
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
const mockUpdateTask = vi.mocked(updateTask)
const mockGetTask = vi.mocked(getTask)

const panelTask: Task = {
  id: 7,
  project_id: null,
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
            project_id: null,
            start_time: '09:00',
            end_time: '10:00',
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
            project_id: null,
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
            project_id: null,
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
            project_id: null,
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
})
