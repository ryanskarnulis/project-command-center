import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
import {
  addDependency,
  listDependencies,
} from '../../api/taskDependencies'
import { createUnscopedTask, listAllTasks, updateTask } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TasksPage } from './TasksPage'

vi.mock('../../api/tasks', () => ({
  createTask: vi.fn(),
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getTask: vi.fn(),
  listAllTasks: vi.fn(),
  listTasks: vi.fn(),
  markTaskDone: vi.fn(),
  updateTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  createProject: vi.fn(),
  deleteProject: vi.fn(),
  listProjects: vi.fn(),
  updateProject: vi.fn(),
}))

vi.mock('../../api/taskDependencies', () => ({
  listDependencies: vi.fn(),
  addDependency: vi.fn(),
  removeDependency: vi.fn(),
}))

const mockListAllTasks = vi.mocked(listAllTasks)
const mockUpdateTask = vi.mocked(updateTask)
const mockListProjects = vi.mocked(listProjects)
const mockCreateUnscopedTask = vi.mocked(createUnscopedTask)
const mockListDependencies = vi.mocked(listDependencies)
const mockAddDependency = vi.mocked(addDependency)

const baseTask: Task = {
  id: 1,
  project_id: null,
  inbox_item_id: null,
  parent_task_id: null,
  title: 'Fix the VPN',
  description: null,
  status: 'accepted',
  priority: 'medium',
  due_date: null,
  estimated_minutes: null,
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
  is_blocked: false,
}

const baseProject: Project = {
  id: 42,
  name: 'Infra',
  description: null,
  system_key: null,
  is_protected: false,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
}

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListAllTasks.mockResolvedValue([baseTask])
    mockUpdateTask.mockResolvedValue({ ...baseTask, priority: 'urgent' })
    mockListProjects.mockResolvedValue([baseProject])
    mockListDependencies.mockResolvedValue([])
    mockAddDependency.mockResolvedValue({
      id: 1,
      task_id: 1,
      depends_on_task_id: 2,
      depends_on_title: 'Rotate the keys',
      depends_on_status: 'accepted',
      depends_on_done: false,
    })
  })

  afterEach(() => {
    cleanup()
  })

  function renderGlobal() {
    return render(
      <MemoryRouter initialEntries={['/tasks']}>
        <TasksPage />
      </MemoryRouter>,
    )
  }

  it('renders the task list', async () => {
    renderGlobal()
    expect(await screen.findByText('Fix the VPN')).toBeInTheDocument()
  })

  it('opens the edit modal when Edit is clicked', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    expect(screen.getByRole('dialog', { name: 'Edit task' })).toBeInTheDocument()
    expect(screen.getByLabelText('Priority')).toHaveValue('medium')
  })

  it('calls updateTask with the changed priority and closes the modal', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    await user.selectOptions(screen.getByLabelText('Priority'), 'urgent')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ priority: 'urgent' }),
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes the modal on Cancel without calling updateTask', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(mockUpdateTask).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows overdue badge for a past due_date', async () => {
    mockListAllTasks.mockResolvedValue([{ ...baseTask, due_date: '2026-01-01' }])
    renderGlobal()
    const badge = await screen.findByText(/^Due Jan/)
    expect(badge.className).toContain('due-overdue')
  })

  it('shows no due badge for a null due_date', async () => {
    renderGlobal()
    await screen.findByText('Fix the VPN')
    // "Due soon" is the filter checkbox label — look for task due badges specifically
    const dueBadges = screen.queryAllByText(/^Due \w+ \d+/)
    expect(dueBadges).toHaveLength(0)
  })

  it('shows project dropdown in modal with loaded projects', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    const dialog = screen.getByRole('dialog', { name: 'Edit task' })
    const projectSelect = dialog.querySelector('#tf-project') as HTMLSelectElement
    expect(projectSelect).toBeInTheDocument()
    expect(projectSelect).toHaveDisplayValue('— unassigned —')
    expect(projectSelect.options.length).toBeGreaterThan(1)
  })

  it('calls updateTask with chosen project_id when project is selected', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    await user.selectOptions(screen.getByLabelText('Project'), 'Infra')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ project_id: 42 }),
    )
  })

  it('shows a human duration label when estimated_minutes is set', async () => {
    mockListAllTasks.mockResolvedValue([{ ...baseTask, estimated_minutes: 60 }])
    renderGlobal()
    expect(await screen.findByText('~1 hour')).toBeInTheDocument()
  })

  it('calls updateTask with the chosen estimate', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    await user.clear(screen.getByLabelText('Estimate'))
    await user.type(screen.getByLabelText('Estimate'), '30')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockUpdateTask).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ estimated_minutes: 30 }),
    )
  })

  it('shows a Blocked badge for a task with an unfinished dependency', async () => {
    mockListAllTasks.mockResolvedValue([{ ...baseTask, is_blocked: true }])
    renderGlobal()
    expect(await screen.findByText('Blocked')).toBeInTheDocument()
  })

  it('adds a dependency from the edit modal', async () => {
    const user = userEvent.setup()
    mockListAllTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, title: 'Rotate the keys' },
    ])
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    await user.selectOptions(
      screen.getByLabelText('Add dependency'),
      'Rotate the keys',
    )
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(mockAddDependency).toHaveBeenCalledWith(1, 2)
  })

  it('filter by status shows only matching tasks', async () => {
    const user = userEvent.setup()
    mockListAllTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, title: 'A done task', status: 'done' },
    ])
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'done')

    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()
    expect(screen.getByText('A done task')).toBeInTheDocument()
  })

  it('filter by priority shows only matching tasks', async () => {
    const user = userEvent.setup()
    mockListAllTasks.mockResolvedValue([
      baseTask, // medium
      { ...baseTask, id: 2, title: 'Urgent work', priority: 'urgent' },
    ])
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'urgent')

    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()
    expect(screen.getByText('Urgent work')).toBeInTheDocument()
  })

  it('Clear filters button restores all tasks', async () => {
    const user = userEvent.setup()
    mockListAllTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, title: 'A done task', status: 'done' },
    ])
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'done')
    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByText('Fix the VPN')).toBeInTheDocument()
    expect(screen.getByText('A done task')).toBeInTheDocument()
  })

  it('empty filter result shows distinct message', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'urgent')

    expect(screen.getByText('No tasks match the current filters.')).toBeInTheDocument()
    expect(screen.queryByText('No tasks yet.')).not.toBeInTheDocument()
  })

  it('renders a subtask nested under its parent', async () => {
    mockListAllTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, parent_task_id: 1, title: 'Rotate the keys' },
    ])
    renderGlobal()

    const child = await screen.findByText('Rotate the keys')
    // The child lives inside the nested .task-children list, not the root list.
    expect(child.closest('ul.task-children')).not.toBeNull()
  })

  it('creates a subtask with the parent_task_id when Add subtask is used', async () => {
    const user = userEvent.setup()
    mockCreateUnscopedTask.mockResolvedValue({
      ...baseTask,
      id: 2,
      parent_task_id: 1,
      title: 'Rotate the keys',
    })
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Add subtask' }))
    await user.type(
      screen.getByPlaceholderText('Subtask title'),
      'Rotate the keys',
    )
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(mockCreateUnscopedTask).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Rotate the keys', parent_task_id: 1 }),
    )
  })
})
