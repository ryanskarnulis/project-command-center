import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
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

const mockListAllTasks = vi.mocked(listAllTasks)
const mockUpdateTask = vi.mocked(updateTask)
const mockListProjects = vi.mocked(listProjects)
const mockCreateUnscopedTask = vi.mocked(createUnscopedTask)

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
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
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
    const badge = await screen.findByText(/^Due /)
    expect(badge.className).toContain('due-overdue')
  })

  it('shows no badge for a null due_date', async () => {
    renderGlobal()
    await screen.findByText('Fix the VPN')
    expect(screen.queryByText(/^Due /)).not.toBeInTheDocument()
  })

  it('shows project dropdown in modal with loaded projects', async () => {
    const user = userEvent.setup()
    renderGlobal()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Edit' }))

    const projectSelect = screen.getByLabelText('Project')
    expect(projectSelect).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Infra' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '— unassigned —' })).toBeInTheDocument()
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
