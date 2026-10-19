import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { deleteProject, listProjects, purgeProject, restoreProject } from '../../api/projects'
import { deleteTask, purgeTask, restoreTask } from '../../api/tasks'
import {
  emptyTrash,
  getTrash,
  purgeSelected,
  restoreTrashedTask,
  undoProjectRestore,
  undoTaskRestore,
} from '../../api/trash'
import { ApiError } from '../../api/client'
import type { ProjectRestoreUndo, TaskRestoreUndo, Trash } from '../../types/trash'
import { TrashPage } from './TrashPage'
import { TrashCountContext, type TrashCounts } from './trashCountContext'
import { UNDO_LIFETIME_MS } from './useTrashPage'

/* M08f at desktop width (#323): the phone's rows, chips, selection mode and
 * undo bar, reached by pointer and keyboard instead of swipe and sheets. */

const renderPage = () => render(<TrashPage />, { wrapper: MemoryRouter })
const renderWithCounts = (counts: TrashCounts) =>
  render(
    <TrashCountContext.Provider value={{ count: counts.projects + counts.tasks, counts, refresh: async () => {} }}>
      <TrashPage />
    </TrashCountContext.Provider>,
    { wrapper: MemoryRouter },
  )

// Relative to the test run's clock so formatRelative is deterministic ("3 days ago").
const DELETED_AT = new Date(Date.now() - 3 * 86_400_000).toISOString()

vi.mock('../../api/trash', () => ({
  getTrash: vi.fn(),
  emptyTrash: vi.fn(),
  purgeSelected: vi.fn(),
  restoreTrashedTask: vi.fn(),
  undoTaskRestore: vi.fn(),
  undoProjectRestore: vi.fn(),
}))
vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
  restoreProject: vi.fn(),
  purgeProject: vi.fn(),
  deleteProject: vi.fn(),
}))
vi.mock('../../api/tasks', () => ({ restoreTask: vi.fn(), purgeTask: vi.fn(), deleteTask: vi.fn() }))

const mockGetTrash = vi.mocked(getTrash)
const mockEmptyTrash = vi.mocked(emptyTrash)
const mockRestoreProject = vi.mocked(restoreProject)
const mockListProjects = vi.mocked(listProjects)
const mockRestoreTask = vi.mocked(restoreTask)
const mockPurgeProject = vi.mocked(purgeProject)
const mockPurgeTask = vi.mocked(purgeTask)
const mockPurgeSelected = vi.mocked(purgeSelected)
const mockRestoreTrashedTask = vi.mocked(restoreTrashedTask)
const mockUndoTaskRestore = vi.mocked(undoTaskRestore)
const mockUndoProjectRestore = vi.mocked(undoProjectRestore)
const mockDeleteProject = vi.mocked(deleteProject)
const mockDeleteTask = vi.mocked(deleteTask)

const projectUndo: ProjectRestoreUndo = { project_id: 1, restore_event_id: 40 }
const taskUndo = (task_id: number): TaskRestoreUndo => ({
  task_id,
  restore_event_id: 100 + task_id,
  restored_task_ids: [task_id],
  skipped: false,
  deleted_with_task_id: null,
  unskip: null,
})

const project = {
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
}

const task = (id: number, title: string, project_id: number) => ({
  id,
  project_id,
  parent_task_id: null,
  estimated_minutes: null,
  actual_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  next_occurrence_date: null,
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
  title,
  description: null,
  workflow_status: 'open' as const,
  priority: 'medium' as const,
  due_date: '2026-08-04',
  deferred_until: null,
  created_at: '2026-06-01T17:00:00Z',
  updated_at: '2026-06-01T17:00:00Z',
  deleted_at: DELETED_AT,
})

// The task's project (2) is NOT in the trash: it was trashed on its own, so its
// meta line names no project.
const trash: Trash = { projects: [project], tasks: [task(5, 'Pay invoice', 2)] }

/** A row title — the same string can also appear in a task's meta line as its project. */
const title = (text: string) => screen.getByText(text, { selector: '.trash-row-title' })
const queryTitle = (text: string) => screen.queryByText(text, { selector: '.trash-row-title' })
const rowOf = (text: string) => title(text).closest('.trash-row') as HTMLElement

function loadOnce(first: Trash): void {
  mockGetTrash.mockReset()
  mockGetTrash.mockResolvedValueOnce(first).mockResolvedValue({ projects: [], tasks: [] })
}

describe('TrashPage — desktop (M08f)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Desktop width: the phone media query does not match.
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    // Active projects feed the restored-task destination label; none needed here.
    mockListProjects.mockResolvedValue([])
    // First load shows the deleted items; the post-action reload is empty.
    loadOnce(trash)
    mockUndoTaskRestore.mockResolvedValue()
    mockUndoProjectRestore.mockResolvedValue()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders M08f rows: group labels, a two-fact meta line, row actions and foot rows', async () => {
    loadOnce({
      projects: [{ ...project, archived_task_count: 3, purge_task_count: 3 }],
      tasks: [task(5, 'Pay invoice', 2), task(6, 'Rotate keys', 1)],
    })
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Trash' })).toBeInTheDocument()
    expect(screen.getByText('3 items')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Projects' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Tasks' })).toBeInTheDocument()
    // Rows, not cards: when it went, then what restoring brings back.
    expect(within(rowOf('Firewall')).getByText(/Deleted 3 days ago/)).toBeInTheDocument()
    expect(within(rowOf('Firewall')).getByText(/3 tasks restore with it/)).toBeInTheDocument()
    // A task whose project is also trashed names it; status and due date are gone.
    expect(within(rowOf('Rotate keys')).getByText('Firewall')).toBeInTheDocument()
    expect(screen.queryByText(/Open/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Due/)).not.toBeInTheDocument()
    // No retention copy: the purge policy (#321) does not exist.
    expect(screen.queryByText(/days left|removed after/)).not.toBeInTheDocument()
    // The ⋯ sheet's verbs are row actions; Restore without its tasks only where it means something.
    expect(within(rowOf('Firewall')).getByRole('button', { name: 'Restore project Firewall without its tasks' })).toBeInTheDocument()
    expect(within(rowOf('Pay invoice')).queryByRole('button', { name: /without its tasks/ })).not.toBeInTheDocument()
    expect(within(rowOf('Pay invoice')).getByRole('button', { name: 'Delete task Pay invoice forever' })).toBeInTheDocument()
    // The old filter panel, section heads and cards are gone.
    expect(screen.queryByLabelText('Filter by type')).not.toBeInTheDocument()
    expect(screen.queryByText(/Restore all/)).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Select' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Empty trash/ })).toHaveTextContent('Empty trash · 3')
  })

  it('restores a project from the ring with its tasks, no confirm, and undoes it with the receipt', async () => {
    loadOnce({ projects: [{ ...project, archived_task_count: 3, purge_task_count: 3 }], tasks: [] })
    mockRestoreProject.mockResolvedValue({ project, restored_task_count: 3, undo: projectUndo })
    const confirm = vi.spyOn(window, 'confirm')
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Restore project Firewall' }))
    await waitFor(() => expect(mockRestoreProject).toHaveBeenCalledWith(1, true))
    expect(confirm).not.toHaveBeenCalled()
    const bar = await screen.findByRole('status')
    expect(bar).toHaveTextContent('Restored · Firewall')
    await user.click(within(bar).getByRole('button', { name: 'Undo' }))
    // The server's receipt, not an ordinary (cascading) project delete.
    await waitFor(() => expect(mockUndoProjectRestore).toHaveBeenCalledWith(projectUndo))
    expect(mockDeleteProject).not.toHaveBeenCalled()
    expect(await screen.findByText('Moved “Firewall” back to the trash.')).toBeInTheDocument()
  })

  it('restores a project without its tasks from the row action', async () => {
    loadOnce({ projects: [{ ...project, archived_task_count: 3, purge_task_count: 3 }], tasks: [] })
    mockRestoreProject.mockResolvedValue({ project, restored_task_count: 0, undo: projectUndo })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Restore project Firewall without its tasks' }))
    await waitFor(() => expect(mockRestoreProject).toHaveBeenCalledWith(1, false))
    expect(await screen.findByRole('status')).toHaveTextContent('Restored · Firewall')
  })

  it('undoes a task restore with its receipt instead of a cascading delete (#307)', async () => {
    mockRestoreTrashedTask.mockResolvedValue({ task: trash.tasks[0], undo: taskUndo(5) })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Restore task Pay invoice' }))
    expect(mockRestoreTrashedTask).toHaveBeenCalledWith(5)
    await user.click(within(await screen.findByRole('status')).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(mockUndoTaskRestore).toHaveBeenCalledWith(taskUndo(5)))
    expect(mockDeleteTask).not.toHaveBeenCalled()
  })

  it('lets the undo bar expire, then names the project a restored task landed in', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockRestoreTrashedTask.mockResolvedValue({ task: { ...trash.tasks[0], project_id: 7 }, undo: taskUndo(5) })
    mockListProjects.mockResolvedValue([{ id: 7, name: 'Ops' } as Awaited<ReturnType<typeof listProjects>>[number]])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Restore task Pay invoice' }))
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(UNDO_LIFETIME_MS) })
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Restored “Pay invoice” to “Ops”.')
    expect(mockUndoTaskRestore).not.toHaveBeenCalled()
  })

  it('does not arm undo when the restore failed', async () => {
    mockRestoreTrashedTask.mockRejectedValueOnce(new Error('Restore failed'))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Restore task Pay invoice' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Restore failed')
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
  })

  it('purges from the row action after a confirm naming every trashed task it owns (#184, #189)', async () => {
    loadOnce({ projects: [{ ...project, archived_task_count: 1, purge_task_count: 2 }], tasks: [] })
    mockPurgeProject.mockResolvedValue(undefined)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Delete project Firewall forever' }))
    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete “Firewall” and the 2 trashed tasks it owns? This cannot be undone.',
    )
    expect(mockPurgeProject).toHaveBeenCalledWith(1)
    expect(await screen.findByRole('status')).toHaveTextContent(/Permanently deleted .*Firewall.* and 2 tasks./)
    // Purge is not undoable, so no undo bar.
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
  })

  it('does not purge when the confirm is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Delete task Pay invoice forever' }))
    expect(mockPurgeTask).not.toHaveBeenCalled()
  })

  it('filters through the anchored pending panel and shows the chips row whenever the list is filtered', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter trash' }))
    const panel = screen.getByRole('dialog', { name: 'Filters' })
    // The cursor lands in the search field.
    expect(within(panel).getByLabelText('Search trash')).toHaveFocus()
    await user.click(within(panel).getByRole('button', { name: 'Tasks' }))
    // Pending until applied: the list behind is untouched.
    expect(title('Firewall')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: 'Show 1 item' })).toBeInTheDocument()
    await user.type(within(panel).getByLabelText('Search trash'), 'pay{Enter}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(queryTitle('Firewall')).not.toBeInTheDocument()
    expect(title('Pay invoice')).toBeInTheDocument()
    const chips = screen.getByLabelText('Applied filters')
    expect(within(chips).getByRole('button', { name: 'Remove Tasks' })).toBeInTheDocument()
    expect(within(chips).getByRole('button', { name: 'Remove “pay”' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Filter trash' })).toHaveClass('applied')
    // Removing one chip drops just that filter.
    await user.click(within(chips).getByRole('button', { name: 'Remove Tasks' }))
    expect(queryTitle('Firewall')).not.toBeInTheDocument()
    expect(within(screen.getByLabelText('Applied filters')).queryByRole('button', { name: 'Remove Tasks' })).not.toBeInTheDocument()
    await user.click(within(screen.getByLabelText('Applied filters')).getByRole('button', { name: 'Clear' }))
    expect(screen.queryByLabelText('Applied filters')).not.toBeInTheDocument()
    expect(title('Firewall')).toBeInTheDocument()
  })

  it('discards the pending filters on Escape or an outside click', async () => {
    const user = userEvent.setup()
    renderPage()
    const control = await screen.findByRole('button', { name: 'Filter trash' })
    await user.click(control)
    await user.type(screen.getByLabelText('Search trash'), 'pay')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(control).toHaveFocus()
    expect(title('Firewall')).toBeInTheDocument()

    await user.click(control)
    await user.click(screen.getByRole('button', { name: 'Projects' }))
    await user.click(title('Pay invoice'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Applied filters')).not.toBeInTheDocument()
  })

  it('counts what an applied filter shows in the heading sub-line, zero matches included (#364)', async () => {
    const user = userEvent.setup()
    renderPage()
    const sub = () => document.querySelector('.trash-heading-sub')
    await user.click(await screen.findByRole('button', { name: 'Filter trash' }))
    expect(sub()).toHaveTextContent(/^2 items$/)
    await user.click(screen.getByRole('button', { name: 'Tasks' }))
    await user.click(screen.getByRole('button', { name: 'Show 1 item' }))
    expect(sub()).toHaveTextContent(/^1 of 2 items$/)
    await user.click(screen.getByRole('button', { name: 'Filter trash' }))
    await user.type(screen.getByLabelText('Search trash'), 'zzz')
    await user.click(screen.getByRole('button', { name: 'Show 0 items' }))
    expect(screen.getByText('No items match your filters')).toBeInTheDocument()
    expect(sub()).toHaveTextContent(/^0 of 2 items$/)
    await user.click(within(screen.getByLabelText('Applied filters')).getByRole('button', { name: 'Clear' }))
    expect(sub()).toHaveTextContent(/^2 items$/)
  })

  it('shows the no-match state with a Clear action and keeps the chips row above it', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filter trash' }))
    await user.type(screen.getByLabelText('Search trash'), 'zzz')
    await user.click(screen.getByRole('button', { name: 'Show 0 items' }))
    const empty = screen.getByText('No items match your filters').parentElement!
    expect(screen.getByLabelText('Applied filters')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
    await user.click(within(empty).getByRole('button', { name: 'Clear' }))
    expect(title('Firewall')).toBeInTheDocument()
  })

  it('enters selection mode from the foot row, spans both kinds and purges in one server call', async () => {
    loadOnce({ projects: [{ ...project, archived_task_count: 1, purge_task_count: 2 }], tasks: [task(5, 'Pay invoice', 2)] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockPurgeSelected.mockResolvedValue({ projects: 1, tasks: 3 })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    expect(screen.getByText('0 selected')).toBeInTheDocument()
    // Rings, row actions and foot rows give way to checkboxes and the action bar.
    expect(screen.queryByRole('button', { name: /^Restore project/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Delete (project|task) .* forever$/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
    const bar = screen.getByRole('toolbar', { name: 'Selection actions' })
    expect(within(bar).getByRole('button', { name: 'Delete forever' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select project Firewall' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select task Pay invoice' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    await user.click(within(bar).getByRole('button', { name: 'Delete forever' }))
    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete 4 items (1 project, 1 task and 2 trashed tasks it owns)? This cannot be undone.',
    )
    await waitFor(() => expect(mockPurgeSelected).toHaveBeenCalledWith({ project_ids: [1], task_ids: [5] }))
    expect(await screen.findByText('Permanently deleted 4 items.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Trash' })).toBeInTheDocument()
  })

  it('enters selection mode with a Ctrl/⌘-click on a row, without restoring it', async () => {
    renderPage()
    const ring = await screen.findByRole('button', { name: 'Restore task Pay invoice' })
    fireEvent.click(ring, { ctrlKey: true })
    expect(mockRestoreTrashedTask).not.toHaveBeenCalled()
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select task Pay invoice' })).toBeChecked()
  })

  it('leaves selection mode on Escape or Done and drops the selection', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select project Firewall' }))
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Select' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Select' }))
    expect(screen.getByText('0 selected')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
  })

  it('bulk-restores everything Select all covers with one combined notice', async () => {
    loadOnce({ projects: [{ ...project, archived_task_count: 2, purge_task_count: 2 }], tasks: [task(5, 'Pay invoice', 2)] })
    mockRestoreProject.mockResolvedValue({ project, restored_task_count: 2, undo: projectUndo })
    mockRestoreTask.mockResolvedValue(trash.tasks[0])
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Select none' })).toBeInTheDocument()
    await user.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(mockRestoreTask).toHaveBeenCalledWith(5))
    expect(mockRestoreProject).toHaveBeenCalledWith(1, true)
    expect(await screen.findByText('Restored 2 items. Brought back 2 tasks.')).toBeInTheDocument()
  })

  it('keeps bulk-restoring when an item is already gone from trash, but surfaces a real failure', async () => {
    // Restoring a skipped occurrence can purge a subtask selected alongside it;
    // its 404 means "already gone", not a failed batch (BUG-11).
    loadOnce({ projects: [], tasks: [task(5, 'Parent', 2), task(6, 'Child', 2), task(7, 'Other', 2)] })
    mockRestoreTask
      .mockResolvedValueOnce(trash.tasks[0])
      .mockRejectedValueOnce(new ApiError(404, { detail: 'No deleted task with that id' }))
      .mockRejectedValueOnce(new ApiError(500, { detail: 'boom' }))
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    await user.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(mockRestoreTask).toHaveBeenCalledTimes(3))
    expect(await screen.findByText('Restored 1 of 2 items.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('boom')
  })

  it('empties the whole trash from the foot row after the confirm', async () => {
    mockEmptyTrash.mockResolvedValue({ projects: 1, tasks: 1 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /Empty trash/ }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Permanently delete all 2 items in trash'))
    expect(mockEmptyTrash).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent(/Permanently deleted 2 items/)
    expect(await screen.findByText('Nothing in the trash')).toBeInTheDocument()
  })

  it('names both numbers when Empty trash also removes archived tasks (#363)', async () => {
    // 2 items on the page; the project holds 3 archived tasks the purge takes too.
    loadOnce({ projects: [{ ...project, archived_task_count: 3, purge_task_count: 3 }], tasks: [task(5, 'Pay invoice', 2)] })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderWithCounts({ projects: 1, tasks: 1, purge_total: 5 })
    const foot = await screen.findByRole('button', { name: /Empty trash/ })
    expect(screen.getByText('2 items')).toBeInTheDocument()
    expect(foot).toHaveTextContent('Empty trash · 2 items, 5 with archived tasks')
    // The confirm keeps the server's exact removable total.
    await user.click(foot)
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Permanently delete all 5 items in trash'))
  })

  it('keeps the plain Empty trash count when it matches the item count', async () => {
    renderWithCounts({ projects: 1, tasks: 1, purge_total: 2 })
    const foot = await screen.findByRole('button', { name: /Empty trash/ })
    expect(foot).toHaveTextContent(/^Empty trash · 2$/)
    expect(foot).not.toHaveTextContent(/archived/)
  })

  it('renders skeleton rows while loading, then the empty state without controls', async () => {
    let resolve: (value: Trash) => void = () => {}
    mockGetTrash.mockReset()
    mockGetTrash.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    renderPage()
    expect(screen.getByLabelText('Loading trash')).toBeInTheDocument()
    act(() => resolve({ projects: [], tasks: [] }))
    expect(await screen.findByText('Nothing in the trash')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filter trash' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
  })

  it('renders an error alert when loading fails', async () => {
    mockGetTrash.mockReset()
    mockGetTrash.mockRejectedValue(new Error('boom'))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
