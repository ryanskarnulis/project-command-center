import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
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
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T00:00:00',
  updated_at: '2026-06-01T00:00:00',
  is_blocked: false,
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
})
