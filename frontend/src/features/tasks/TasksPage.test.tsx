import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects } from '../../api/projects'
import { listDependencies, listDependents } from '../../api/taskDependencies'
import {
  createTask,
  createUnscopedTask,
  deleteTask,
  getSubtasks,
  getTask,
  listAllTasks,
  listCompletedTasks,
  listTasks,
  markTaskDone,
  reopenTask,
  skipOccurrence,
  updateTask,
} from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TasksPage } from './TasksPage'

vi.mock('../../api/tasks', () => ({
  createTask: vi.fn(),
  createUnscopedTask: vi.fn(),
  deleteTask: vi.fn(),
  getSubtasks: vi.fn(),
  getTask: vi.fn(),
  getTaskSeries: vi.fn(),
  listAllTasks: vi.fn(),
  listCompletedTasks: vi.fn(() => Promise.resolve([])),
  listTasks: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  skipOccurrence: vi.fn(),
  stopRecurrence: vi.fn(),
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
  listDependents: vi.fn(),
  addDependency: vi.fn(),
  removeDependency: vi.fn(),
}))

const mockListTasks = vi.mocked(listTasks)
const mockListAllTasks = vi.mocked(listAllTasks)
const mockCreateTask = vi.mocked(createTask)
const mockListCompletedTasks = vi.mocked(listCompletedTasks)
const mockListProjects = vi.mocked(listProjects)
const mockCreateUnscopedTask = vi.mocked(createUnscopedTask)
const mockDeleteTask = vi.mocked(deleteTask)
const mockMarkTaskDone = vi.mocked(markTaskDone)
const mockReopenTask = vi.mocked(reopenTask)
const mockSkipOccurrence = vi.mocked(skipOccurrence)
const mockUpdateTask = vi.mocked(updateTask)
const mockGetTask = vi.mocked(getTask)
const mockGetSubtasks = vi.mocked(getSubtasks)
const mockListDependencies = vi.mocked(listDependencies)
const mockListDependents = vi.mocked(listDependents)

const baseTask: Task = {
  id: 1,
  project_id: 1,
  parent_task_id: null,
  title: 'Fix the VPN',
  description: null,
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  deferred_until: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

const baseProject: Project = {
  id: 42,
  name: 'Infra',
  description: null,
  system_key: null,
  sort_order: 0,
  is_protected: false,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
}

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListTasks.mockResolvedValue([baseTask])
    // Read by the peek panel's dependency picker, not by the page itself.
    mockListAllTasks.mockResolvedValue([baseTask])
    mockListProjects.mockResolvedValue([baseProject])
    mockMarkTaskDone.mockResolvedValue({ ...baseTask, workflow_status: 'done' })
    // The peek panel fetches its own data when `?task=` is set.
    mockGetTask.mockResolvedValue(baseTask)
    mockGetSubtasks.mockResolvedValue([])
    mockListDependencies.mockResolvedValue([])
    mockListDependents.mockResolvedValue([])
  })

  afterEach(() => {
    cleanup()
  })

  const PROJECT_PATH = '/projects/1/tasks'

  /**
   * Render the project's Tasks tab. Entries are written as `/tasks?...` for
   * readability and rewritten onto the project route here; the list view is
   * pinned unless an entry picks one, because this surface defaults to the
   * board and most cases below assert list rendering.
   */
  function renderProject(initialEntries: string[] = ['/tasks'], initialIndex = 0) {
    const entries = initialEntries.map((entry) => {
      const [, query = ''] = entry.split('?')
      const params = new URLSearchParams(query)
      if (!params.has('view')) params.set('view', 'list')
      return `${PROJECT_PATH}?${params.toString()}`
    })
    const router = createMemoryRouter(
      [{ path: '/projects/:projectId/tasks', element: <TasksPage /> }],
      { initialEntries: entries, initialIndex },
    )
    return { router, ...render(<RouterProvider router={router} />) }
  }

  it('renders the task list', async () => {
    renderProject()
    expect(await screen.findByText('Fix the VPN')).toBeInTheDocument()
  })

  it('does not show per-row Edit buttons', async () => {
    renderProject()
    await screen.findByText('Fix the VPN')
    expect(
      screen.queryByRole('button', { name: 'Edit' }),
    ).not.toBeInTheDocument()
  })

  it('shows overdue badge for a past due_date', async () => {
    mockListTasks.mockResolvedValue([{ ...baseTask, due_date: '2026-01-01' }])
    renderProject()
    const badge = await screen.findByText(/^Due Jan/)
    expect(badge.className).toContain('due-overdue')
  })

  it('shows no due badge for a null due_date', async () => {
    renderProject()
    await screen.findByText('Fix the VPN')
    // "Due soon" is the filter checkbox label — look for task due badges specifically
    const dueBadges = screen.queryAllByText(/^Due \w+ \d+/)
    expect(dueBadges).toHaveLength(0)
  })

  it('shows a human duration label when estimated_minutes is set', async () => {
    mockListTasks.mockResolvedValue([{ ...baseTask, estimated_minutes: 60 }])
    renderProject()
    expect(await screen.findByText('~1 hour')).toBeInTheDocument()
  })

  it('shows a Blocked badge for a task with an unfinished dependency', async () => {
    mockListTasks.mockResolvedValue([{ ...baseTask, is_blocked: true }])
    renderProject()
    expect(await screen.findByText('Blocked')).toBeInTheDocument()
  })

  it('shows a Blocking badge for a top-level blocker', async () => {
    mockListTasks.mockResolvedValue([
      { ...baseTask, is_blocking: true, blocked_task_count: 2 },
    ])
    renderProject()
    expect(await screen.findByText('Blocking 2 tasks')).toBeInTheDocument()
  })

  it('filter by status shows only matching tasks', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([baseTask])
    // The "Done" view loads the completed archive lazily, not the active list.
    mockListCompletedTasks.mockResolvedValue([
      { ...baseTask, id: 2, title: 'A done task', workflow_status: 'done' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'done')

    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()
    expect(await screen.findByText('A done task')).toBeInTheDocument()
  })

  it('filter by Blocking status shows only top-level blockers', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask,
      {
        ...baseTask,
        id: 2,
        title: 'Shared dependency',
        is_blocking: true,
        blocked_task_count: 3,
      },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'blocking')

    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()
    expect(screen.getByText('Shared dependency')).toBeInTheDocument()
    expect(screen.getByText('Blocking 3 tasks')).toBeInTheDocument()
  })

  it('filter by priority shows only matching tasks', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask, // medium
      { ...baseTask, id: 2, title: 'Urgent work', priority: 'urgent' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'urgent')

    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()
    expect(screen.getByText('Urgent work')).toBeInTheDocument()
  })

  it('Clear filters button restores all tasks', async () => {
    const user = userEvent.setup()
    const doneTask = { ...baseTask, id: 2, title: 'A done task', workflow_status: 'done' as const }
    // Active list omits done tasks; the "Done" view loads them from the archive.
    mockListTasks.mockResolvedValue([baseTask])
    mockListCompletedTasks.mockResolvedValue([doneTask])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'done')
    expect(await screen.findByText('A done task')).toBeInTheDocument()
    expect(screen.queryByText('Fix the VPN')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByText('Fix the VPN')).toBeInTheDocument()
    expect(screen.queryByText('A done task')).not.toBeInTheDocument()
  })

  it('empty filter result shows distinct message', async () => {
    const user = userEvent.setup()
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'urgent')

    expect(screen.getByText('No tasks match the current filters.')).toBeInTheDocument()
    expect(screen.queryByText('No tasks yet.')).not.toBeInTheDocument()
  })

  it('filters by search text in titles and descriptions', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      { ...baseTask, description: 'Repair the private tunnel' },
      { ...baseTask, id: 2, title: 'Urgent work', description: 'Patch hosts' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.type(screen.getByLabelText('Search tasks'), 'tunnel')

    expect(screen.getByText('Fix the VPN')).toBeInTheDocument()
    expect(screen.queryByText('Urgent work')).not.toBeInTheDocument()
  })

  it('hydrates filters and sort mode from query params', async () => {
    mockListTasks.mockResolvedValue([
      { ...baseTask, title: 'Medium tunnel', description: 'Repair the private tunnel' },
      {
        ...baseTask,
        id: 2,
        title: 'Urgent tunnel',
        description: 'Repair the private tunnel',
        priority: 'urgent',
      },
    ])

    renderProject(['/tasks?search=tunnel&priority=urgent&sort=newest'])

    expect(await screen.findByText('Urgent tunnel')).toBeInTheDocument()
    expect(screen.queryByText('Medium tunnel')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Search tasks')).toHaveValue('tunnel')
    expect(screen.getByLabelText('Filter by priority')).toHaveValue('urgent')
    expect(screen.getByLabelText('Sort tasks')).toHaveValue('newest')
  })

  it('writes filter and sort changes back to canonical URL params', async () => {
    const user = userEvent.setup()
    const { router } = renderProject()

    await screen.findByText('Fix the VPN')
    await user.type(screen.getByLabelText('Search tasks'), 'vpn')
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'urgent')
    await user.selectOptions(screen.getByLabelText('Sort tasks'), 'due_date')

    await waitFor(() => {
      const params = new URLSearchParams(router.state.location.search)
      expect(params.get('search')).toBe('vpn')
      expect(params.get('priority')).toBe('urgent')
      expect(params.get('sort')).toBe('due_date')
    })
  })

  it('clears task filter query params when Clear filters is clicked', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      { ...baseTask, priority: 'urgent' },
    ])
    const { router } = renderProject(['/tasks?priority=urgent'])

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))

    await waitFor(() => {
      expect(new URLSearchParams(router.state.location.search).get('priority')).toBeNull()
    })
  })

  it('restores filters from browser back and forward navigation', async () => {
    mockListTasks.mockResolvedValue([
      { ...baseTask, title: 'Urgent work', priority: 'urgent' },
      { ...baseTask, id: 2, title: 'High work', priority: 'high' },
    ])
    const { router } = renderProject(
      ['/tasks?priority=urgent', '/tasks?priority=high'],
      1,
    )

    expect(await screen.findByText('High work')).toBeInTheDocument()
    expect(screen.queryByText('Urgent work')).not.toBeInTheDocument()

    await act(async () => {
      await router.navigate(-1)
    })

    expect(await screen.findByText('Urgent work')).toBeInTheDocument()
    expect(screen.queryByText('High work')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Filter by priority')).toHaveValue('urgent')
  })

  it('sorts tasks with the selected sort mode', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      { ...baseTask, due_date: '2026-06-20' },
      { ...baseTask, id: 2, title: 'Soon work', due_date: '2026-06-10' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.selectOptions(screen.getByLabelText('Sort tasks'), 'due_date')

    const taskLinks = screen
      .getAllByRole('link')
      .map((link) => link.getAttribute('aria-label'))
      // Drop the surface's own chrome: the back link and the two project tabs
      // are unlabelled links, and only task cards carry an aria-label.
      .filter((label) => label !== null)
    expect(taskLinks).toEqual(['Soon work', 'Fix the VPN'])
  })

  it('marks a task done from the compact row action', async () => {
    const user = userEvent.setup()
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(
      screen.getByRole('button', { name: 'Mark Fix the VPN done' }),
    )

    expect(mockMarkTaskDone).toHaveBeenCalledWith(1)
  })

  it('keeps subtasks collapsed until the parent toggle is clicked', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, parent_task_id: 1, title: 'Rotate the keys' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    expect(screen.queryByText('Rotate the keys')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Subtasks (1)' }))
    const child = screen.getByText('Rotate the keys')
    expect(child.closest('ul.task-children')).not.toBeNull()
  })

  it('keeps nested subtasks hidden until their parent subtask is expanded', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, parent_task_id: 1, title: 'Rotate the keys' },
      { ...baseTask, id: 3, parent_task_id: 2, title: 'Verify rotation' },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Subtasks (1)' }))

    expect(screen.getByText('Rotate the keys')).toBeInTheDocument()
    expect(screen.queryByText('Verify rotation')).not.toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Subtasks (1)' })[1])
    expect(screen.getByText('Verify rotation')).toBeInTheDocument()
  })

  it('excludes subtasks from the board view, showing only parent tasks', async () => {
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, parent_task_id: 1, title: 'Rotate the keys' },
    ])
    const { router } = renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Board' }))

    expect(screen.getByText('Fix the VPN')).toBeInTheDocument()
    expect(screen.queryByText('Rotate the keys')).not.toBeInTheDocument()
    // Board is the default view here, so selecting it drops the param rather
    // than pinning it — the list view is the one that has to be spelled out.
    await waitFor(() =>
      expect(new URLSearchParams(router.state.location.search).get('view')).toBeNull(),
    )
  })

  it('keeps an orphaned subtask on the board and in its done archive (#93)', async () => {
    // A live child whose parent was trashed (e.g. with its project) is an
    // effective root — the server says so via is_effective_top_level, and the
    // board must show it rather than filter it out with the real subtasks.
    const user = userEvent.setup()
    mockListTasks.mockResolvedValue([
      baseTask,
      { ...baseTask, id: 2, parent_task_id: 1, title: 'Rotate the keys' },
      {
        ...baseTask,
        id: 3,
        parent_task_id: 99,
        is_effective_top_level: true,
        title: 'Orphaned by a deleted project',
      },
    ])
    mockListCompletedTasks.mockResolvedValue([
      {
        ...baseTask,
        id: 4,
        parent_task_id: 99,
        is_effective_top_level: true,
        workflow_status: 'done',
        title: 'Orphaned and finished',
      },
    ])
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Board' }))

    expect(screen.getByText('Orphaned by a deleted project')).toBeInTheDocument()
    expect(await screen.findByText('Orphaned and finished')).toBeInTheDocument()
    expect(screen.queryByText('Rotate the keys')).not.toBeInTheDocument()
  })

  it('creates a subtask with the parent_task_id when Add subtask is used', async () => {
    const user = userEvent.setup()
    mockCreateTask.mockResolvedValue({
      ...baseTask,
      id: 2,
      parent_task_id: 1,
      title: 'Rotate the keys',
    })
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Add subtask' }))
    const titleInput = screen.getByPlaceholderText('Subtask title')
    await user.type(titleInput, 'Rotate the keys')
    // Scope to the composer's form — the quick-add bar has an "Add" button too.
    const composer = titleInput.closest('form') as HTMLFormElement
    await user.click(within(composer).getByRole('button', { name: 'Add' }))

    // No explicit project in the payload, so it files through the page's own
    // project via the scoped endpoint.
    expect(mockCreateTask).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ title: 'Rotate the keys', parent_task_id: 1 }),
    )
  })

  it('creates a task from the quick-add bar with parsed tokens', async () => {
    const user = userEvent.setup()
    mockCreateUnscopedTask.mockResolvedValue({
      ...baseTask,
      id: 3,
      title: 'Renew TLS cert',
      priority: 'high',
      project_id: 42,
    })
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.type(
      screen.getByLabelText('Quick add task'),
      'Renew TLS cert !high #infra{Enter}',
    )

    await waitFor(() =>
      expect(mockCreateUnscopedTask).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({
          title: 'Renew TLS cert',
          priority: 'high',
          project_id: 42,
        }),
      ),
    )
    expect(screen.getByLabelText('Quick add task')).toHaveValue('')
  })

  it('keeps failed list mutations local and skips their follow-up reloads', async () => {
    const user = userEvent.setup()
    mockDeleteTask.mockRejectedValue(new Error('Delete failed'))
    mockMarkTaskDone.mockRejectedValue(new Error('Complete failed'))
    mockUpdateTask.mockRejectedValue(new Error('Update failed'))
    renderProject()
    await screen.findByText('Fix the VPN')

    await user.click(screen.getByRole('button', { name: 'Delete Fix the VPN' }))
    await waitFor(() => expect(mockDeleteTask).toHaveBeenCalledWith(1))
    expect(mockListTasks).toHaveBeenCalledTimes(1)

    await user.click(
      screen.getByRole('button', { name: 'Mark Fix the VPN done' }),
    )
    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(1))
    expect(mockListTasks).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Priority: medium' }))
    await user.click(screen.getByRole('button', { name: 'high' }))
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(1, { priority: 'high' }),
    )

    const taskCard = screen.getByRole('link', { name: 'Fix the VPN' })
    await user.click(
      within(taskCard).getByRole('button', { name: 'Status: Open' }),
    )
    await user.click(screen.getByRole('button', { name: 'In progress' }))
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(1, {
        workflow_status: 'in_progress',
      }),
    )
  })

  it('keeps a failed subtask draft open for retry', async () => {
    const user = userEvent.setup()
    mockCreateTask.mockRejectedValue(new Error('Create failed'))
    renderProject()

    await screen.findByText('Fix the VPN')
    await user.click(screen.getByRole('button', { name: 'Add subtask' }))
    const titleInput = screen.getByPlaceholderText('Subtask title')
    await user.type(titleInput, 'Keep this subtask')
    const composer = titleInput.closest('form') as HTMLFormElement
    await user.click(within(composer).getByRole('button', { name: 'Add' }))

    await waitFor(() =>
      expect(mockCreateTask).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ title: 'Keep this subtask' }),
      ),
    )
    expect(screen.getByPlaceholderText('Subtask title')).toHaveValue(
      'Keep this subtask',
    )
  })

  it('swallows a rejected recurring-task skip', async () => {
    mockListTasks.mockResolvedValue([
      {
        ...baseTask,
        repeat_interval: { unit: 'week', every: 1 },
        recurrence_id: 'series-1',
      },
    ])
    mockSkipOccurrence.mockRejectedValue(new Error('Skip failed'))
    renderProject()
    await screen.findByText('Fix the VPN')

    const taskCard = screen.getByRole('link', { name: 'Fix the VPN' })
    fireEvent.click(
      within(taskCard).getByRole('button', { name: 'Status: Open' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Skip occurrence…' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Skip occurrence' }),
    )

    await waitFor(() => expect(mockSkipOccurrence).toHaveBeenCalledWith(1))
    expect(
      screen.queryByRole('dialog', { name: 'Skip occurrence' }),
    ).not.toBeInTheDocument()
  })

  it('swallows rejected edits from a completed list card', async () => {
    const user = userEvent.setup()
    mockListCompletedTasks.mockResolvedValue([
      { ...baseTask, workflow_status: 'done' },
    ])
    mockUpdateTask.mockRejectedValue(new Error('Update failed'))
    renderProject(['/tasks?status=done'])
    await screen.findByText('Fix the VPN')

    await user.click(screen.getByRole('button', { name: 'Priority: medium' }))
    await user.click(screen.getByRole('button', { name: 'high' }))

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(1, { priority: 'high' }),
    )
    expect(mockListCompletedTasks).toHaveBeenCalledTimes(1)
  })

  it('files a modal-created task in the selected project', async () => {
    const user = userEvent.setup()
    mockCreateUnscopedTask.mockResolvedValue({
      ...baseTask,
      id: 4,
      title: 'File me right',
      project_id: 42,
    })
    renderProject(['/tasks?new=1'])

    const modal = await screen.findByRole('dialog', { name: 'Add task' })
    await user.type(within(modal).getByLabelText('Title'), 'File me right')
    await user.selectOptions(within(modal).getByLabelText('Project'), '42')
    await user.click(within(modal).getByRole('button', { name: 'Save' }))

    // The unscoped endpoint is the one that honors the payload's project.
    await waitFor(() =>
      expect(mockCreateUnscopedTask).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'File me right', project_id: 42 }),
      ),
    )
  })

  it('deep-links ?task= to the peek panel over the list', async () => {
    renderProject(['/tasks?task=1'])

    const panel = await screen.findByRole('dialog', { name: 'Task details' })
    expect(panel).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByLabelText('Task title')).toHaveValue('Fix the VPN'),
    )
    // The list is still rendered behind the panel.
    expect(screen.getByRole('heading', { name: 'Tasks' })).toBeInTheDocument()
  })

  it('opens the peek panel from a task card without navigating away', async () => {
    const user = userEvent.setup()
    const { router } = renderProject()

    await user.click(await screen.findByRole('link', { name: 'Fix the VPN' }))

    expect(await screen.findByRole('dialog', { name: 'Task details' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/projects/1/tasks')
    expect(new URLSearchParams(router.state.location.search).get('task')).toBe('1')
  })

  it('closes the peek panel on Escape and drops the task param', async () => {
    const user = userEvent.setup()
    const { router } = renderProject(['/tasks?task=1'])

    await screen.findByRole('dialog', { name: 'Task details' })
    await user.keyboard('{Escape}')

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Task details' })).not.toBeInTheDocument(),
    )
    expect(new URLSearchParams(router.state.location.search).get('task')).toBeNull()
  })

  it('moves a done card to In progress with one atomic PATCH (#148)', async () => {
    const user = userEvent.setup()
    const doneTask = {
      ...baseTask,
      id: 2,
      title: 'A done task',
      workflow_status: 'done' as const,
    }
    mockListCompletedTasks.mockResolvedValue([doneTask])
    renderProject(['/tasks?view=board'])

    await screen.findByText('A done task')
    await user.click(screen.getByRole('button', { name: 'Status: Done' }))
    await user.click(screen.getByRole('button', { name: 'In progress' }))

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(2, { workflow_status: 'in_progress' }),
    )
    // Reopen-then-patch could half-commit as Open; the transition is one write.
    expect(mockReopenTask).not.toHaveBeenCalled()
  })

  it('leaves a done card done when the in-progress write rejects (#148)', async () => {
    const user = userEvent.setup()
    const doneTask = {
      ...baseTask,
      id: 2,
      title: 'A done task',
      workflow_status: 'done' as const,
    }
    mockListCompletedTasks.mockResolvedValue([doneTask])
    mockUpdateTask.mockRejectedValue(new Error('Task is blocked'))
    renderProject(['/tasks?view=board'])

    await screen.findByText('A done task')
    await user.click(screen.getByRole('button', { name: 'Status: Done' }))
    await user.click(screen.getByRole('button', { name: 'In progress' }))

    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(2, { workflow_status: 'in_progress' }),
    )
    // Nothing was committed: no reopen ran, so the task is still Done.
    expect(mockReopenTask).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Status: Done' })).toBeInTheDocument()
  })

  it('still asks for closed projects so the Project sort can name one (#133)', async () => {
    const closedProject: Project = {
      ...baseProject,
      id: 7,
      name: 'Archived Migration',
      closed_at: '2026-06-15T10:00:00Z',
    }
    // Closing a project leaves its tasks open, and the "Project" sort resolves
    // names from this list — omit the closed ones and those tasks sort as
    // "Unassigned". The cross-project label and filter went with `/tasks`.
    mockListProjects.mockResolvedValue([baseProject, closedProject])
    renderProject()

    await screen.findByText('Fix the VPN')
    expect(mockListProjects).toHaveBeenCalledWith(true)
    expect(screen.queryByLabelText('Filter by project')).not.toBeInTheDocument()
  })

  it('omits closed projects from the create modal picker (#133)', async () => {
    const closedProject: Project = {
      ...baseProject,
      id: 7,
      name: 'Archived Migration',
      closed_at: '2026-06-15T10:00:00Z',
    }
    mockListProjects.mockResolvedValue([baseProject, closedProject])
    renderProject(['/tasks?new=1'])

    const modal = await screen.findByRole('dialog', { name: 'Add task' })
    const picker = within(modal).getByLabelText('Project')
    await waitFor(() =>
      expect(within(picker).getByText('Infra')).toBeInTheDocument(),
    )
    expect(within(picker).queryByText('Archived Migration')).toBeNull()
  })

  it('keeps the peek panel open when switching to the board view', async () => {
    const user = userEvent.setup()
    const { router } = renderProject(['/tasks?task=1'])

    await screen.findByRole('dialog', { name: 'Task details' })
    await user.click(screen.getByRole('button', { name: 'Board' }))

    const params = new URLSearchParams(router.state.location.search)
    // Board is the default view, so it is spelled by the param's absence.
    expect(params.get('view')).toBeNull()
    expect(params.get('task')).toBe('1')
    expect(screen.getByRole('dialog', { name: 'Task details' })).toBeInTheDocument()
  })
})
