import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskCard } from './TaskCard'

afterEach(cleanup)

const base: Task = {
  id: 7,
  project_id: 1,
  inbox_item_id: null,
  parent_task_id: null,
  title: 'Patch the router',
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
  created_at: '2026-06-01T00:00:00',
  updated_at: '2026-06-01T00:00:00',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

const homeNetworkProject: Project = {
  id: 1,
  name: 'HomeNetwork',
  description: null,
  system_key: null,
  is_protected: false,
  created_at: '',
  updated_at: '',
}

function render_card(task: Partial<Task> = {}, projects?: Project[]) {
  render(
    <MemoryRouter>
      <TaskCard task={{ ...base, ...task }} projects={projects} />
    </MemoryRouter>,
  )
}

describe('TaskCard', () => {
  it('renders the task title', () => {
    render_card()
    expect(screen.getByText('Patch the router')).toBeInTheDocument()
  })

  it('links to /tasks/:id', () => {
    render_card()
    expect(screen.getByRole('link')).toHaveAttribute('href', '/tasks/7')
  })

  it('shows the workflow status badge', () => {
    render_card()
    expect(screen.getByText('Open')).toBeInTheDocument()
  })

  it('shows priority badge', () => {
    render_card()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('shows Blocked badge when blocked and not done', () => {
    render_card({ is_blocked: true })
    expect(screen.getByText('Blocked')).toBeInTheDocument()
  })

  it('shows Blocking badge for a top-level blocker', () => {
    render_card({ is_blocking: true, blocked_task_count: 2 })
    expect(screen.getByText('Blocking 2 tasks')).toBeInTheDocument()
    expect(screen.queryByText('Blocked')).not.toBeInTheDocument()
  })

  it('hides Blocked badge when task is done', () => {
    render_card({ is_blocked: true, workflow_status: 'done' })
    expect(screen.queryByText('Blocked')).not.toBeInTheDocument()
  })

  it('shows estimate badge', () => {
    render_card({ estimated_minutes: 120 })
    expect(screen.getByText('~2 hours')).toBeInTheDocument()
  })

  it('shows project name when projects provided', () => {
    render_card({}, [homeNetworkProject])
    expect(screen.getByText('HomeNetwork')).toBeInTheDocument()
  })

  it('hides project name when projects not provided', () => {
    render_card()
    expect(screen.queryByText('HomeNetwork')).not.toBeInTheDocument()
  })

  describe('complete circle', () => {
    function render_with_complete(task: Partial<Task> = {}) {
      const onComplete = vi.fn()
      render(
        <MemoryRouter>
          <TaskCard task={{ ...base, ...task }} onComplete={onComplete} />
        </MemoryRouter>,
      )
      return onComplete
    }

    it('is hidden when onComplete is not provided', () => {
      render_card()
      expect(
        screen.queryByRole('button', { name: 'Mark Patch the router done' }),
      ).not.toBeInTheDocument()
    })

    it('calls onComplete on click without navigating', () => {
      const onComplete = render_with_complete()
      fireEvent.click(
        screen.getByRole('button', { name: 'Mark Patch the router done' }),
      )
      expect(onComplete).toHaveBeenCalledOnce()
    })

    it('is disabled for a blocked task', () => {
      render_with_complete({ is_blocked: true })
      expect(
        screen.getByRole('button', { name: 'Mark Patch the router done' }),
      ).toBeDisabled()
    })

    it('is disabled when status rolls up from subtasks', () => {
      render_with_complete({ has_subtasks: true })
      expect(
        screen.getByRole('button', { name: 'Mark Patch the router done' }),
      ).toBeDisabled()
    })

    it('is hidden on a done task', () => {
      render_with_complete({ workflow_status: 'done' })
      expect(
        screen.queryByRole('button', { name: 'Mark Patch the router done' }),
      ).not.toBeInTheDocument()
    })
  })

  describe('recurrence', () => {
    it('shows the next occurrence beside the repeat badge', () => {
      render_card({
        repeat_interval: { unit: 'week', every: 1 },
        next_occurrence_date: '2026-07-10',
      })
      expect(screen.getByText(/weekly/)).toBeInTheDocument()
      expect(screen.getByText(/next Jul 10/)).toBeInTheDocument()
    })

    it('omits the next-occurrence hint when the date is null', () => {
      render_card({ repeat_interval: { unit: 'week', every: 1 }, next_occurrence_date: null })
      expect(screen.queryByText(/next /)).not.toBeInTheDocument()
    })

    it('offers "Skip occurrence…" in the status menu for a recurring task', () => {
      const onSkip = vi.fn()
      render(
        <MemoryRouter>
          <TaskCard
            task={{ ...base, repeat_interval: { unit: 'week', every: 1 } }}
            onUpdate={vi.fn()}
            onSkipOccurrence={onSkip}
          />
        </MemoryRouter>,
      )
      fireEvent.click(screen.getByRole('button', { name: /Status:/ }))
      const skip = screen.getByRole('button', { name: 'Skip occurrence…' })
      fireEvent.click(skip)
      expect(onSkip).toHaveBeenCalledOnce()
    })

    it('hides the skip action for a non-recurring task', () => {
      render(
        <MemoryRouter>
          <TaskCard task={base} onUpdate={vi.fn()} onSkipOccurrence={vi.fn()} />
        </MemoryRouter>,
      )
      fireEvent.click(screen.getByRole('button', { name: /Status:/ }))
      expect(
        screen.queryByRole('button', { name: 'Skip occurrence…' }),
      ).not.toBeInTheDocument()
    })
  })

  it('sets the task id as drag data for sidebar filing and kanban drops', () => {
    render_card()
    const setData = vi.fn()
    fireEvent.dragStart(screen.getByRole('link'), {
      dataTransfer: { setData, effectAllowed: '' },
    })
    expect(setData).toHaveBeenCalledWith('application/x-pcc-task', '7')
    expect(setData).toHaveBeenCalledWith('text/plain', '7')
  })
})
