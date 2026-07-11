import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  deleteProject,
  getProject,
  getProjectActivity,
  updateProject,
} from '../../api/projects'
import { getTask, listCompletedTasks, listTasks, updateTask } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { ProjectDetailPage } from './ProjectDetailPage'

vi.mock('../../api/projects', () => ({
  deleteProject: vi.fn(),
  getProject: vi.fn(),
  updateProject: vi.fn(),
  getProjectActivity: vi.fn(),
  listProjects: vi.fn(() => Promise.resolve([])),
}))

vi.mock('../../api/tasks', () => ({
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getSubtasks: vi.fn(() => Promise.resolve([])),
  getTask: vi.fn(),
  getTaskSeries: vi.fn(),
  listAllTasks: vi.fn(() => Promise.resolve([])),
  listCompletedTasks: vi.fn(),
  listTasks: vi.fn(),
  markTaskDone: vi.fn(),
  skipOccurrence: vi.fn(),
  stopRecurrence: vi.fn(),
  updateTask: vi.fn(),
}))

vi.mock('../../api/taskDependencies', () => ({
  addDependency: vi.fn(),
  listDependencies: vi.fn(() => Promise.resolve([])),
  listDependents: vi.fn(() => Promise.resolve([])),
  removeDependency: vi.fn(),
}))

const project: Project = {
  id: 7,
  name: 'Firewall',
  description: 'Edge hardening',
  system_key: null,
  sort_order: 0,
  is_protected: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
}

const task: Task = {
  id: 3,
  project_id: 7,
  parent_task_id: null,
  title: 'Patch the router',
  description: null,
  workflow_status: 'open',
  priority: 'high',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
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

const mockDeleteProject = vi.mocked(deleteProject)
const mockGetProject = vi.mocked(getProject)
const mockUpdateProject = vi.mocked(updateProject)
const mockGetTask = vi.mocked(getTask)
const mockUpdateTask = vi.mocked(updateTask)
const mockListTasks = vi.mocked(listTasks)
const mockListCompleted = vi.mocked(listCompletedTasks)
const mockGetProjectActivity = vi.mocked(getProjectActivity)

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/projects/7']}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="/dashboard" element={<main>Dashboard page</main>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetProject.mockResolvedValue(project)
    mockListTasks.mockResolvedValue([task])
    mockListCompleted.mockResolvedValue([])
    mockUpdateProject.mockImplementation(async (_id, patch) => ({ ...project, ...patch }))
    mockGetProjectActivity.mockResolvedValue([])
  })

  afterEach(cleanup)

  it('renders the project name, its tasks, and a View-all link', async () => {
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    expect(await screen.findByText('Patch the router')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /View all tasks/ }),
    ).toHaveAttribute('href', '/projects/7/tasks')
  })

  it('saves the name inline on blur', async () => {
    const user = userEvent.setup()
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()

    await waitFor(() =>
      expect(mockUpdateProject).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ name: 'Edge Firewall' }),
      ),
    )
  })

  it('does not save when the name is cleared to blank', async () => {
    const user = userEvent.setup()
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    await user.clear(name)
    await user.tab()

    expect(mockUpdateProject).not.toHaveBeenCalled()
    expect(await screen.findByText('Name is required')).toBeInTheDocument()
  })

  it('guards refresh/close only while a field holds an unsaved edit', async () => {
    const user = userEvent.setup()
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')
    renderDetail()

    const description = await screen.findByLabelText('Project description')
    await waitFor(() => expect(description).toHaveValue('Edge hardening'))
    expect(addSpy).not.toHaveBeenCalledWith('beforeunload', expect.any(Function))

    // Type without blurring: the edit is unsaved, so the guard attaches.
    await user.type(description, ' more')
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function)),
    )

    cleanup()
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  })

  it('deletes the project after confirmation and navigates to the dashboard', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDeleteProject.mockResolvedValue(undefined)
    renderDetail()

    await user.click(
      await screen.findByRole('button', { name: 'Delete project' }),
    )

    expect(confirmSpy).toHaveBeenCalledWith(
      'Delete "Firewall"? Its active tasks move to General.',
    )
    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith(7))
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('hides the delete action on protected projects', async () => {
    mockGetProject.mockResolvedValue({ ...project, is_protected: true })
    renderDetail()

    await screen.findByLabelText('Project name')
    expect(
      screen.queryByRole('button', { name: 'Delete project' }),
    ).not.toBeInTheDocument()
  })

  it('mounts the activity feed for the project', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Activity' }))
    expect(await screen.findByText('No activity yet.')).toBeInTheDocument()
    expect(mockGetProjectActivity).toHaveBeenCalled()
  })

  it('opens the peek panel from a task card and refetches tasks after a panel edit', async () => {
    const user = userEvent.setup()
    mockGetTask.mockResolvedValue(task)
    mockUpdateTask.mockResolvedValue({ ...task, priority: 'urgent' })
    renderDetail()

    await user.click(await screen.findByRole('link', { name: 'Patch the router' }))

    const panel = await screen.findByRole('dialog', { name: 'Task details' })
    expect(panel).toBeInTheDocument()
    await waitFor(() => expect(mockGetTask).toHaveBeenCalledWith(3))
    expect(mockListTasks).toHaveBeenCalledTimes(1)

    // A chip edit inside the panel PATCHes and asks the host list to refetch.
    // Scope to the panel: task cards now render their own priority pill.
    await user.click(await within(panel).findByRole('button', { name: 'Priority: high' }))
    await user.click(screen.getByRole('button', { name: 'urgent' }))

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(3, expect.objectContaining({ priority: 'urgent' })),
    )
    await waitFor(() => expect(mockListTasks).toHaveBeenCalledTimes(2))
  })
})
