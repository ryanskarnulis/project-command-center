import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  closeProject,
  deleteProject,
  getProject,
  getProjectActivity,
  reopenProject,
  updateProject,
} from '../../api/projects'
import {
  getTask,
  listCompletedTasks,
  listTasks,
  markTaskDone,
  updateTask,
} from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { ProjectDetailPage } from './ProjectDetailPage'

vi.mock('../../api/projects', () => ({
  closeProject: vi.fn(),
  deleteProject: vi.fn(),
  getProject: vi.fn(),
  reopenProject: vi.fn(),
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
const mockCloseProject = vi.mocked(closeProject)
const mockGetProject = vi.mocked(getProject)
const mockReopenProject = vi.mocked(reopenProject)
const mockUpdateProject = vi.mocked(updateProject)
const mockGetTask = vi.mocked(getTask)
const mockUpdateTask = vi.mocked(updateTask)
const mockMarkTaskDone = vi.mocked(markTaskDone)
const mockListTasks = vi.mocked(listTasks)
const mockListCompleted = vi.mocked(listCompletedTasks)
const mockGetProjectActivity = vi.mocked(getProjectActivity)

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  // Attach a no-op catch so an intentionally rejected deferred is never unhandled.
  promise.catch(() => undefined)
  return { promise, resolve, reject }
}

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

/** Same mounted route, plus a link that only changes `:projectId`. */
function renderDetailWithSwitcher() {
  return render(
    <MemoryRouter initialEntries={['/projects/7']}>
      <Link to="/projects/8">Open project 8</Link>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
        <Route path="/dashboard" element={<main>Dashboard page</main>} />
      </Routes>
    </MemoryRouter>,
  )
}

function primeMocks() {
  vi.clearAllMocks()
  mockGetProject.mockResolvedValue(project)
  mockListTasks.mockResolvedValue([task])
  mockListCompleted.mockResolvedValue([])
  mockUpdateProject.mockImplementation(async (_id, patch) => ({ ...project, ...patch }))
  mockGetProjectActivity.mockResolvedValue([])
}

describe('ProjectDetailPage', () => {
  beforeEach(primeMocks)

  afterEach(cleanup)

  it('counts root tasks as open and calls subtasks out beside them', async () => {
    mockListTasks.mockResolvedValue([
      task,
      { ...task, id: 4, parent_task_id: 3, title: 'Order the patch cable' },
      { ...task, id: 5, parent_task_id: 3, title: 'Schedule the window' },
    ])
    mockListCompleted.mockResolvedValue([{ ...task, id: 100, workflow_status: 'done' }])
    renderDetail()

    expect(await screen.findByText('1 open · 2 subtasks · 1 done')).toBeInTheDocument()
  })

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

  it('does not let a stale PATCH response revert a newer inline edit', async () => {
    const user = userEvent.setup()
    const nameResponse = deferred<Project>()
    const descriptionResponse = deferred<Project>()
    mockUpdateProject
      .mockReturnValueOnce(nameResponse.promise)
      .mockReturnValueOnce(descriptionResponse.promise)
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    const description = screen.getByLabelText('Project description')

    // PATCH A: rename, response delayed.
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()
    // PATCH B: new description, response delivered first.
    await user.clear(description)
    await user.type(description, 'Perimeter hardening')
    await user.tab()
    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(2))

    descriptionResponse.resolve({
      ...project,
      name: 'Edge Firewall',
      description: 'Perimeter hardening',
    })
    await waitFor(() => expect(description).toHaveValue('Perimeter hardening'))

    // The older rename response carries the pre-edit description; it must not land.
    nameResponse.resolve({ ...project, name: 'Edge Firewall' })
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
    expect(description).toHaveValue('Perimeter hardening')
    expect(name).toHaveValue('Edge Firewall')
  })

  it('ignores a stale PATCH failure so the newest write keeps the save line', async () => {
    const user = userEvent.setup()
    const nameResponse = deferred<Project>()
    const descriptionResponse = deferred<Project>()
    mockUpdateProject
      .mockReturnValueOnce(nameResponse.promise)
      .mockReturnValueOnce(descriptionResponse.promise)
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    const description = screen.getByLabelText('Project description')

    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()
    await user.clear(description)
    await user.type(description, 'Perimeter hardening')
    await user.tab()
    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(2))

    descriptionResponse.resolve({
      ...project,
      name: 'Edge Firewall',
      description: 'Perimeter hardening',
    })
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())

    nameResponse.reject(new Error('boom'))
    await nameResponse.promise.catch(() => undefined)
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
    expect(screen.queryByText('boom')).not.toBeInTheDocument()
  })

  it('discards a PATCH that resolves after navigating to another project', async () => {
    const user = userEvent.setup()
    const otherProject: Project = { ...project, id: 8, name: 'Perimeter', description: 'Other' }
    mockGetProject.mockImplementation(async (pid: number) =>
      pid === 8 ? otherProject : project,
    )
    const nameResponse = deferred<Project>()
    mockUpdateProject.mockReturnValueOnce(nameResponse.promise)
    renderDetailWithSwitcher()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))

    // PATCH A on project 7, response held open.
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()
    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledWith(7, { name: 'Edge Firewall' }))

    // Same mounted route, new :projectId.
    await user.click(screen.getByRole('link', { name: 'Open project 8' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter'),
    )

    nameResponse.resolve({ ...project, name: 'Edge Firewall' })
    await waitFor(() => expect(mockGetProject).toHaveBeenCalledWith(8))
    expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter')
    expect(screen.getByLabelText('Project description')).toHaveValue('Other')
    expect(screen.queryByText('Saved')).not.toBeInTheDocument()
    expect(screen.queryByText('Saving…')).not.toBeInTheDocument()
  })

  it('discards a PATCH failure from a project we already navigated away from', async () => {
    const user = userEvent.setup()
    const otherProject: Project = { ...project, id: 8, name: 'Perimeter', description: 'Other' }
    mockGetProject.mockImplementation(async (pid: number) =>
      pid === 8 ? otherProject : project,
    )
    const nameResponse = deferred<Project>()
    mockUpdateProject.mockReturnValueOnce(nameResponse.promise)
    renderDetailWithSwitcher()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()
    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('link', { name: 'Open project 8' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter'),
    )

    nameResponse.reject(new Error('boom'))
    await nameResponse.promise.catch(() => undefined)
    await waitFor(() => expect(mockGetProject).toHaveBeenCalledWith(8))
    expect(screen.queryByText('boom')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter')
  })

  it('does not show the previous project’s done count while the new one loads', async () => {
    const user = userEvent.setup()
    const otherProject: Project = { ...project, id: 8, name: 'Perimeter', description: 'Other' }
    mockGetProject.mockImplementation(async (pid: number) => (pid === 8 ? otherProject : project))
    mockListTasks.mockImplementation(async (pid?: number) =>
      pid === 8 ? [{ ...task, id: 9, project_id: 8, title: 'Swap the switch' }] : [task],
    )
    const doneA = Array.from({ length: 8 }, (_, i) => ({ ...task, id: 100 + i }))
    const completedB = deferred<Task[]>()
    mockListCompleted.mockImplementation((pid?: number) =>
      pid === 8 ? completedB.promise : Promise.resolve(doneA),
    )
    renderDetailWithSwitcher()

    expect(await screen.findByText('1 open · 8 done')).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'Open project 8' }))
    await waitFor(() => expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter'))

    // B's completed request is still pending: A's 8 must not carry over.
    expect(screen.getByText('1 open · 0 done')).toBeInTheDocument()

    completedB.resolve([{ ...task, id: 200, project_id: 8 }])
    expect(await screen.findByText('1 open · 1 done')).toBeInTheDocument()
  })

  it('does not keep a stale done count when the new project’s request fails', async () => {
    const user = userEvent.setup()
    const otherProject: Project = { ...project, id: 8, name: 'Perimeter', description: 'Other' }
    mockGetProject.mockImplementation(async (pid: number) => (pid === 8 ? otherProject : project))
    mockListTasks.mockImplementation(async (pid?: number) =>
      pid === 8 ? [{ ...task, id: 9, project_id: 8, title: 'Swap the switch' }] : [task],
    )
    const doneA = Array.from({ length: 8 }, (_, i) => ({ ...task, id: 100 + i }))
    const completedB = deferred<Task[]>()
    mockListCompleted.mockImplementation((pid?: number) =>
      pid === 8 ? completedB.promise : Promise.resolve(doneA),
    )
    renderDetailWithSwitcher()

    expect(await screen.findByText('1 open · 8 done')).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'Open project 8' }))
    await waitFor(() => expect(screen.getByLabelText('Project name')).toHaveValue('Perimeter'))

    completedB.reject(new Error('boom'))
    await completedB.promise.catch(() => undefined)
    // Best-effort failure: the count stays at zero rather than reverting to A's.
    await waitFor(() => expect(screen.getByText('1 open · 0 done')).toBeInTheDocument())
    expect(screen.queryByText('1 open · 8 done')).not.toBeInTheDocument()
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
      'Delete “Firewall”? Its active tasks move to Trash with it. You can restore them together.',
    )
    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith(7))
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('stays on the project when delete rejects', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDeleteProject.mockRejectedValue(new Error('Delete failed'))
    renderDetail()

    await user.click(
      await screen.findByRole('button', { name: 'Delete project' }),
    )
    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith(7))
    expect(screen.getByLabelText('Project name')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard page')).not.toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('keeps the close action available when closing rejects', async () => {
    const user = userEvent.setup()
    mockCloseProject.mockRejectedValue(new Error('Close failed'))
    renderDetail()

    await user.click(
      await screen.findByRole('button', { name: 'Close project' }),
    )
    await waitFor(() => expect(mockCloseProject).toHaveBeenCalledWith(7))
    expect(
      screen.getByRole('button', { name: 'Close project' }),
    ).toBeInTheDocument()
  })

  it('keeps the reopen action available when reopening rejects', async () => {
    const user = userEvent.setup()
    mockGetProject.mockResolvedValue({
      ...project,
      closed_at: '2026-07-01T00:00:00Z',
    })
    mockReopenProject.mockRejectedValue(new Error('Reopen failed'))
    renderDetail()

    await user.click(
      await screen.findByRole('button', { name: 'Reopen project' }),
    )
    await waitFor(() => expect(mockReopenProject).toHaveBeenCalledWith(7))
    expect(
      screen.getByRole('button', { name: 'Reopen project' }),
    ).toBeInTheDocument()
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

  it('does not refetch tasks after rejected card mutations', async () => {
    const user = userEvent.setup()
    mockMarkTaskDone.mockRejectedValue(new Error('Complete failed'))
    mockUpdateTask.mockRejectedValue(new Error('Update failed'))
    renderDetail()
    await screen.findByText('Patch the router')
    const taskCard = screen.getByRole('link', { name: 'Patch the router' })

    await user.click(
      within(taskCard).getByRole('button', {
        name: 'Mark Patch the router done',
      }),
    )
    await waitFor(() => expect(mockMarkTaskDone).toHaveBeenCalledWith(3))
    expect(mockListTasks).toHaveBeenCalledTimes(1)

    await user.click(
      within(taskCard).getByRole('button', { name: 'Priority: high' }),
    )
    await user.click(screen.getByRole('button', { name: 'urgent' }))
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(3, { priority: 'urgent' }),
    )
    expect(mockListTasks).toHaveBeenCalledTimes(1)

    await user.click(
      within(taskCard).getByRole('button', { name: 'Status: Open' }),
    )
    await user.click(screen.getByRole('button', { name: 'In progress' }))
    await waitFor(() =>
      expect(mockUpdateTask).toHaveBeenCalledWith(3, {
        workflow_status: 'in_progress',
      }),
    )
    expect(mockListTasks).toHaveBeenCalledTimes(1)
  })
})

describe('ProjectDetailPage (mobile, M06f)', () => {
  const originalShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal')
  const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close')
  const subtask: Task = { ...task, id: 4, parent_task_id: 3, title: 'Order the patch cable' }
  const done = [
    { ...task, id: 100, workflow_status: 'done' as const },
    { ...task, id: 101, workflow_status: 'done' as const },
  ]

  beforeEach(() => {
    primeMocks()
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    )
    // jsdom has no native dialog lifecycle; focus/backdrop/swipe are verified in Chromium.
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
      configurable: true,
      value: function (this: HTMLDialogElement) { this.open = true },
    })
    Object.defineProperty(HTMLDialogElement.prototype, 'close', {
      configurable: true,
      value: function (this: HTMLDialogElement) { this.open = false },
    })
    mockListTasks.mockResolvedValue([task, subtask])
    mockListCompleted.mockResolvedValue(done)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    if (originalShowModal) Object.defineProperty(HTMLDialogElement.prototype, 'showModal', originalShowModal)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal')
    if (originalClose) Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close')
  })

  it('renders the brief with root-only counts and no task list', async () => {
    renderDetail()

    expect(await screen.findByRole('heading', { level: 1, name: 'Firewall' })).toBeInTheDocument()
    // The Tasks tab's derivation: one root open, its subtask called out, done separately.
    expect(await screen.findByText('1 open')).toBeInTheDocument()
    expect(screen.getByText('1 subtask')).toBeInTheDocument()
    expect(screen.getByText('2 done')).toBeInTheDocument()
    expect(screen.getByText('On Track')).toBeInTheDocument()
    // Progress keeps every filed task in the denominator: 2 done of 4.
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')

    expect(screen.queryByText('Patch the router')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /View all tasks/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Dashboard/ })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tasks' })).toHaveAttribute('href', '/projects/7/tasks')
    expect(screen.getByText('Edge hardening')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Activity' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('omits zero counts from the meta line', async () => {
    mockListTasks.mockResolvedValue([task])
    mockListCompleted.mockResolvedValue([])
    renderDetail()

    expect(await screen.findByText('1 open')).toBeInTheDocument()
    expect(screen.queryByText(/subtask/)).not.toBeInTheDocument()
    expect(screen.queryByText(/done/)).not.toBeInTheDocument()
  })

  it('edits the name on tap and commits on Enter', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Firewall' }))
    const name = screen.getByLabelText('Project name')
    expect(name).toHaveValue('Firewall')
    expect(name).toHaveFocus()
    await user.clear(name)
    await user.type(name, 'Edge Firewall{Enter}')

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledWith(7, { name: 'Edge Firewall' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Edge Firewall' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Project name')).not.toBeInTheDocument()
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('reverts the name draft on Escape without saving', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Firewall' }))
    await user.type(screen.getByLabelText('Project name'), ' v2{Escape}')

    expect(await screen.findByRole('heading', { level: 1, name: 'Firewall' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Project name')).not.toBeInTheDocument()
    expect(mockUpdateProject).not.toHaveBeenCalled()
  })

  it('keeps the name field open with its error when cleared to blank', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Firewall' }))
    await user.clear(screen.getByLabelText('Project name'))
    await user.tab()

    expect(await screen.findByText('Name is required')).toBeInTheDocument()
    expect(screen.getByLabelText('Project name')).toBeInTheDocument()
    expect(mockUpdateProject).not.toHaveBeenCalled()
  })

  it('reopens the name field with the draft intact when the write fails', async () => {
    const user = userEvent.setup()
    mockUpdateProject.mockRejectedValueOnce(new Error('boom'))
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Firewall' }))
    const name = screen.getByLabelText('Project name')
    await user.clear(name)
    await user.type(name, 'Edge')
    await user.tab()

    expect(await screen.findByText('Not saved')).toBeInTheDocument()
    expect(await screen.findByLabelText('Project name')).toHaveValue('Edge')
    expect(screen.getByText('boom')).toBeInTheDocument()
  })

  it('edits the description on tap and saves on blur', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByText('Edge hardening'))
    const description = screen.getByLabelText('Project description')
    expect(description).toHaveValue('Edge hardening')
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
    await user.type(description, ' and more')
    await user.tab()
    await user.tab()

    await waitFor(() =>
      expect(mockUpdateProject).toHaveBeenCalledWith(7, { description: 'Edge hardening and more' }),
    )
    expect(await screen.findByText('Edge hardening and more')).toBeInTheDocument()
    expect(screen.queryByLabelText('Project description')).not.toBeInTheDocument()
  })

  it('prompts for a missing description and opens the editor from the pencil', async () => {
    const user = userEvent.setup()
    mockGetProject.mockResolvedValue({ ...project, description: null })
    renderDetail()

    expect(await screen.findByText('Add a description')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit description' }))
    expect(screen.getByLabelText('Project description')).toHaveValue('')
    await user.click(screen.getByRole('button', { name: 'Done' }))

    expect(screen.queryByLabelText('Project description')).not.toBeInTheDocument()
    expect(mockUpdateProject).not.toHaveBeenCalled()
  })

  it('closes the project from the action sheet', async () => {
    const user = userEvent.setup()
    mockCloseProject.mockResolvedValue({ ...project, closed_at: '2026-09-05T00:00:00Z' })
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Project actions' }))
    const sheet = screen.getByRole('dialog', { name: 'Project actions' })
    expect(within(sheet).getByRole('button', { name: 'Delete project' })).toBeInTheDocument()
    await user.click(within(sheet).getByRole('button', { name: 'Close project' }))

    await waitFor(() => expect(mockCloseProject).toHaveBeenCalledWith(7))
    expect(screen.queryByRole('dialog', { name: 'Project actions' })).not.toBeInTheDocument()
    expect(await screen.findByText('Closed')).toBeInTheDocument()
  })

  it('offers Reopen for a closed project', async () => {
    const user = userEvent.setup()
    mockGetProject.mockResolvedValue({ ...project, closed_at: '2026-07-01T00:00:00Z' })
    mockReopenProject.mockResolvedValue(project)
    renderDetail()

    expect(await screen.findByText('Closed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Project actions' }))
    await user.click(screen.getByRole('button', { name: 'Reopen project' }))

    await waitFor(() => expect(mockReopenProject).toHaveBeenCalledWith(7))
    expect(await screen.findByText('On Track')).toBeInTheDocument()
  })

  it('deletes from the sheet after confirmation and navigates home', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDeleteProject.mockResolvedValue(undefined)
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Project actions' }))
    await user.click(screen.getByRole('button', { name: 'Delete project' }))

    expect(confirmSpy).toHaveBeenCalledWith(
      'Delete “Firewall”? Its active tasks move to Trash with it. You can restore them together.',
    )
    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith(7))
    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  it('renders no actions control for a protected project but keeps the name editable', async () => {
    mockGetProject.mockResolvedValue({ ...project, is_protected: true })
    renderDetail()

    expect(await screen.findByRole('button', { name: 'Firewall' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Project actions' })).not.toBeInTheDocument()
  })

  it('shows the status word alone when the task fetch fails', async () => {
    mockListTasks.mockRejectedValue(new Error('Tasks unavailable'))
    renderDetail()

    expect(await screen.findByRole('alert')).toHaveTextContent('Tasks unavailable')
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText(/open/)).not.toBeInTheDocument()
    expect(screen.queryByText(/done/)).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Firewall' })).toBeInTheDocument()
  })
})
