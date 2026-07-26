import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listProjects, purgeProject, restoreProject } from '../../api/projects'
import { purgeTask, restoreTask } from '../../api/tasks'
import { emptyTrash, getTrash, purgeSelected } from '../../api/trash'
import { ApiError } from '../../api/client'
import type { Trash } from '../../types/trash'
import { TrashPage } from './TrashPage'

const renderPage = () => render(<TrashPage />, { wrapper: MemoryRouter })

// Relative to the test run's clock so formatRelative is deterministic ("3 days ago").
const DELETED_AT = new Date(Date.now() - 3 * 86_400_000).toISOString()

vi.mock('../../api/trash', () => ({
  getTrash: vi.fn(),
  emptyTrash: vi.fn(),
  purgeSelected: vi.fn(),
}))
vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
  restoreProject: vi.fn(),
  purgeProject: vi.fn(),
}))
vi.mock('../../api/tasks', () => ({ restoreTask: vi.fn(), purgeTask: vi.fn() }))

const mockGetTrash = vi.mocked(getTrash)
const mockEmptyTrash = vi.mocked(emptyTrash)
const mockRestoreProject = vi.mocked(restoreProject)
const mockListProjects = vi.mocked(listProjects)
const mockRestoreTask = vi.mocked(restoreTask)
const mockPurgeProject = vi.mocked(purgeProject)
const mockPurgeTask = vi.mocked(purgeTask)
const mockPurgeSelected = vi.mocked(purgeSelected)

const trash: Trash = {
  projects: [
    {
      id: 1,
      name: 'Firewall',
      description: null,
      system_key: null,
      sort_order: 0,
      is_protected: false,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
      archived_task_count: 0,
      purge_task_count: 0,
    },
  ],
  tasks: [
    {
      id: 5,
      project_id: null,
      parent_task_id: null,
      estimated_minutes: null,
      repeat_interval: null,
      recurrence_id: null,
      next_occurrence_date: null,
      is_blocked: false,
      is_blocking: false,
      blocked_task_count: 0,
      has_subtasks: false,
      title: 'Pay invoice',
      description: null,
      workflow_status: 'open',
      priority: 'medium',
      due_date: null,
      deferred_until: null,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
    },
  ],
}

describe('TrashPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Active projects feed the restored-task destination label; none needed here.
    mockListProjects.mockResolvedValue([])
    // First load shows the deleted items; the post-restore reload is empty.
    mockGetTrash
      .mockResolvedValueOnce(trash)
      .mockResolvedValue({ projects: [], tasks: [] })
  })

  it('lists deleted items and restores a project', async () => {
    const user = userEvent.setup()
    mockRestoreProject.mockResolvedValue({ project: trash.projects[0], restored_task_count: 0 })

    renderPage()

    expect(await screen.findByText('Firewall')).toBeInTheDocument()
    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    // Each card shows a relative deleted-time label (fixtures deleted 3 days ago).
    expect(screen.getAllByText('Deleted 3 days ago').length).toBe(2)

    await user.click(
      screen.getByRole('button', { name: 'Restore project Firewall' }),
    )

    // No archived tasks → restored without bringing tasks back.
    expect(mockRestoreProject).toHaveBeenCalledWith(1, false)
    await waitFor(() =>
      expect(screen.getByText('Trash is empty.')).toBeInTheDocument(),
    )
  })

  it('offers to bring tasks back when restoring a project that has them', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockGetTrash.mockReset()
    mockGetTrash
      .mockResolvedValueOnce({
        ...trash,
        projects: [{ ...trash.projects[0], archived_task_count: 3 }],
      })
      .mockResolvedValue({ projects: [], tasks: [] })
    mockRestoreProject.mockResolvedValue({ project: trash.projects[0], restored_task_count: 3 })

    renderPage()

    expect(await screen.findByText('3 tasks to restore')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Restore project Firewall' }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(mockRestoreProject).toHaveBeenCalledWith(1, true)
    await waitFor(() =>
      expect(screen.getByText(/Brought back 3 tasks|with 3 tasks/)).toBeInTheDocument(),
    )
    confirmSpy.mockRestore()
  })

  it('exposes a restore handler for tasks', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Restore task Pay invoice' }),
    )
    expect(mockRestoreTask).toHaveBeenCalledWith(5)
  })

  it('names the project a restored task actually lands in', async () => {
    const user = userEvent.setup()
    // The restore response carries the task's real (rehomed-or-original) project;
    // the notice should name it, not assume General.
    mockRestoreTask.mockResolvedValue({ ...trash.tasks[0], project_id: 7 })
    mockListProjects.mockResolvedValue([
      { id: 7, name: 'Firewall' } as Awaited<ReturnType<typeof listProjects>>[number],
    ])

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Restore task Pay invoice' }),
    )
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Restored .*Pay invoice.* to .*Firewall/,
    )
  })

  it('renders the loading state before trash resolves', () => {
    renderPage()
    expect(screen.getByText('Loading trash…')).toBeInTheDocument()
  })

  it('renders the empty state when trash is empty', async () => {
    // mockReset (not clearAllMocks) drops the beforeEach once→trash queue.
    mockGetTrash.mockReset()
    mockGetTrash.mockResolvedValue({ projects: [], tasks: [] })

    renderPage()

    expect(await screen.findByText('Trash is empty.')).toBeInTheDocument()
  })

  it('renders an error alert when loading fails', async () => {
    mockGetTrash.mockReset()
    mockGetTrash.mockRejectedValue(new Error('boom'))

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })

  it('narrows results by search and restores them on Clear', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Firewall')
    await user.type(screen.getByLabelText('Search trash'), 'firewall')

    expect(screen.getByText('Firewall')).toBeInTheDocument()
    expect(screen.queryByText('Pay invoice')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
  })

  it('isolates a single section with the type filter', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')

    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    expect(screen.queryByText('Firewall')).not.toBeInTheDocument()
  })

  it('shows a no-match message when the search hides everything', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Firewall')
    await user.type(screen.getByLabelText('Search trash'), 'nothing matches this')

    expect(screen.getByText('No items match your search.')).toBeInTheDocument()
    expect(screen.queryByText('Firewall')).not.toBeInTheDocument()
  })

  it('shows a success notice naming the restored item', async () => {
    const user = userEvent.setup()
    mockRestoreProject.mockResolvedValue({ project: trash.projects[0], restored_task_count: 0 })

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Restore project Firewall' }),
    )

    expect(await screen.findByRole('status')).toHaveTextContent(/Restored project .*Firewall/)
  })

  it('restores a whole section with the restore-all button', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])

    renderPage()

    // Isolate the Tasks section so there's a single restore-all button. With the
    // type filter active the button names its true scope: the 1 shown task.
    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('button', { name: 'Restore 1 shown' }))

    expect(mockRestoreTask).toHaveBeenCalledWith(5)
    expect(await screen.findByRole('status')).toHaveTextContent(/Restored 1 task./)
  })

  it('purges a task after the user confirms', async () => {
    const user = userEvent.setup()
    mockPurgeTask.mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Delete task Pay invoice forever' }),
    )

    expect(confirm).toHaveBeenCalled()
    expect(mockPurgeTask).toHaveBeenCalledWith(5)
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted .*Pay invoice/,
    )
    confirm.mockRestore()
  })

  it('names every trashed task it owns in the single-project purge confirm', async () => {
    // BUG #184 / #189: purging a project destroys every trashed task it owns —
    // archived-with-it plus independently trashed — so the confirm (and the
    // notice) must say so instead of naming only the project.
    const user = userEvent.setup()
    mockGetTrash.mockReset()
    mockGetTrash
      .mockResolvedValueOnce({
        projects: [
          { ...trash.projects[0], archived_task_count: 1, purge_task_count: 2 },
        ],
        tasks: [],
      })
      .mockResolvedValue({ projects: [], tasks: [] })
    mockPurgeProject.mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Delete project Firewall forever' }),
    )

    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete “Firewall” and the 2 trashed tasks it owns? This cannot be undone.',
    )
    expect(mockPurgeProject).toHaveBeenCalledWith(1)
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted .*Firewall.* and 2 tasks./,
    )
    confirm.mockRestore()
  })

  it('counts every owned trashed task in the bulk project purge confirm', async () => {
    const user = userEvent.setup()
    mockGetTrash.mockReset()
    mockGetTrash
      .mockResolvedValueOnce({
        projects: [
          { ...trash.projects[0], archived_task_count: 1, purge_task_count: 2 },
        ],
        tasks: [],
      })
      .mockResolvedValue({ projects: [], tasks: [] })
    mockPurgeSelected.mockResolvedValue({ projects: 1, tasks: 2 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await screen.findByText('Firewall')
    await user.click(screen.getByRole('checkbox', { name: 'Select project Firewall' }))
    await user.click(screen.getByRole('button', { name: 'Delete selected' }))

    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete 3 items (1 project and 2 trashed tasks)? This cannot be undone.',
    )
    expect(mockPurgeSelected).toHaveBeenCalledWith({ project_ids: [1], task_ids: [] })
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted 1 project. 2 archived tasks went with it./,
    )
    confirm.mockRestore()
  })

  it('does not purge when the user cancels the confirm', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)

    renderPage()

    await user.click(
      await screen.findByRole('button', { name: 'Delete project Firewall forever' }),
    )

    expect(mockPurgeProject).not.toHaveBeenCalled()
    confirm.mockRestore()
  })

  it('bulk-restores the checked items via Restore selected', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])

    renderPage()

    await screen.findByText('Firewall')
    // Isolate Tasks so a single Select all / bulk bar is present.
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select task Pay invoice' }))
    await user.click(screen.getByRole('button', { name: 'Restore selected' }))

    expect(mockRestoreTask).toHaveBeenCalledWith(5)
    expect(await screen.findByRole('status')).toHaveTextContent(/Restored 1 task./)
  })

  it('bulk-purges the checked items via Delete selected after confirm', async () => {
    const user = userEvent.setup()
    mockPurgeSelected.mockResolvedValue({ projects: 0, tasks: 1 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select task Pay invoice' }))
    await user.click(screen.getByRole('button', { name: 'Delete selected' }))

    expect(confirm).toHaveBeenCalled()
    // One request for the whole selection, not one per id.
    expect(mockPurgeSelected).toHaveBeenCalledTimes(1)
    expect(mockPurgeSelected).toHaveBeenCalledWith({ project_ids: [], task_ids: [5] })
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted 1 task./,
    )
    confirm.mockRestore()
  })

  it('reports honest success when a parent and its cascaded child are purged together', async () => {
    // BUG-11: purging the parent takes the child's row with it. The old per-id
    // loop 404'd on the child and cried failure over a purge that fully worked.
    const user = userEvent.setup()
    const parent = { ...trash.tasks[0], id: 5, title: 'Parent', has_subtasks: true }
    const child = { ...trash.tasks[0], id: 6, title: 'Child', parent_task_id: 5 }
    mockGetTrash.mockReset()
    mockGetTrash
      .mockResolvedValueOnce({ projects: [], tasks: [parent, child] })
      .mockResolvedValue({ projects: [], tasks: [] })
    mockPurgeSelected.mockResolvedValue({ projects: 0, tasks: 2 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await screen.findByText('Parent')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Delete selected' }))

    expect(mockPurgeSelected).toHaveBeenCalledWith({
      project_ids: [],
      task_ids: [5, 6],
    })
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted 2 tasks./,
    )
    // The whole point: no error banner, and no "stopped at the first error".
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    confirm.mockRestore()
  })

  it('keeps bulk-restoring when an item is already gone from trash', async () => {
    // Restoring a skipped occurrence can purge a subtask selected alongside it;
    // its 404 means "already gone", not a failed batch.
    const user = userEvent.setup()
    const first = { ...trash.tasks[0], id: 5, title: 'Parent' }
    const second = { ...trash.tasks[0], id: 6, title: 'Child' }
    mockGetTrash.mockReset()
    mockGetTrash
      .mockResolvedValueOnce({ projects: [], tasks: [first, second] })
      .mockResolvedValue({ projects: [], tasks: [] })
    mockRestoreTask
      .mockResolvedValueOnce(first)
      .mockRejectedValueOnce(new ApiError(404, { detail: 'No deleted task with that id' }))

    renderPage()

    await screen.findByText('Parent')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Restore selected' }))

    expect(mockRestoreTask).toHaveBeenCalledTimes(2)  // didn't stop at the 404
    expect(await screen.findByRole('status')).toHaveTextContent(/Restored 1 task./)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('surfaces a real bulk-restore failure', async () => {
    // The 404 tolerance must not swallow genuine errors.
    const user = userEvent.setup()
    mockRestoreTask.mockRejectedValue(new ApiError(500, { detail: 'boom' }))

    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select all' }))
    await user.click(screen.getByRole('button', { name: 'Restore selected' }))

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  it('select-all checks every item in a section', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('checkbox', { name: 'Select all' }))

    expect(
      screen.getByRole('checkbox', { name: 'Select task Pay invoice' }),
    ).toBeChecked()
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })

  it('empties the whole trash after the user confirms', async () => {
    const user = userEvent.setup()
    mockEmptyTrash.mockResolvedValue({ projects: 1, tasks: 1 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Empty trash' }))

    expect(confirm).toHaveBeenCalled()
    expect(mockEmptyTrash).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted 2 items/,
    )
    await waitFor(() =>
      expect(screen.getByText('Trash is empty.')).toBeInTheDocument(),
    )
    confirm.mockRestore()
  })
})
