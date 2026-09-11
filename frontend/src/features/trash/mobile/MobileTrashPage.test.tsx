import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { deleteProject, listProjects, purgeProject, restoreProject } from '../../../api/projects'
import { deleteTask, purgeTask, restoreTask } from '../../../api/tasks'
import { emptyTrash, getTrash, purgeSelected } from '../../../api/trash'
import type { Trash } from '../../../types/trash'
import { TrashPage } from '../TrashPage'
import { SWIPE_COMMIT_PX } from './TrashSwipeRow'

/* M08f — the mobile tree, reached through TrashPage at phone width. */

vi.mock('../../../api/trash', () => ({
  getTrash: vi.fn(),
  emptyTrash: vi.fn(),
  purgeSelected: vi.fn(),
}))
vi.mock('../../../api/projects', () => ({
  listProjects: vi.fn(),
  restoreProject: vi.fn(),
  purgeProject: vi.fn(),
  deleteProject: vi.fn(),
}))
vi.mock('../../../api/tasks', () => ({ restoreTask: vi.fn(), purgeTask: vi.fn(), deleteTask: vi.fn() }))

const mockGetTrash = vi.mocked(getTrash)
const mockRestoreProject = vi.mocked(restoreProject)
const mockRestoreTask = vi.mocked(restoreTask)
const mockDeleteProject = vi.mocked(deleteProject)
const mockDeleteTask = vi.mocked(deleteTask)
const mockPurgeTask = vi.mocked(purgeTask)
const mockPurgeProject = vi.mocked(purgeProject)
const mockPurgeSelected = vi.mocked(purgeSelected)
const mockEmptyTrash = vi.mocked(emptyTrash)

const DELETED_AT = new Date(Date.now() - 2 * 86_400_000).toISOString()

const task = (id: number, title: string, project_id: number) => ({
  id,
  project_id,
  parent_task_id: null,
  estimated_minutes: 45,
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
  priority: 'low' as const,
  due_date: '2026-08-04',
  deferred_until: null,
  created_at: '2026-06-01T17:00:00Z',
  updated_at: '2026-06-01T17:00:00Z',
  deleted_at: DELETED_AT,
})

const trash: Trash = {
  projects: [
    {
      id: 1,
      name: 'Old Wiki Rewrite',
      description: null,
      system_key: null,
      sort_order: 0,
      is_protected: false,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
      archived_task_count: 3,
      purge_task_count: 4,
    },
  ],
  tasks: [task(5, 'Write the old wiki export script', 1), task(6, 'Trial the second UPS unit', 9)],
}

const renderMobile = () => render(<TrashPage />, { wrapper: MemoryRouter })

/** A row title — the same string can also appear in a task's meta line as its project. */
const title = (text: string) => screen.getByText(text, { selector: '.trash-row-title' })
const queryTitle = (text: string) => screen.queryByText(text, { selector: '.trash-row-title' })

/** A pointer drag on a row's content: press, one move past the slop, one to `dx`, release. */
function swipe(element: Element, dx: number): void {
  fireEvent.pointerDown(element, { button: 0, clientX: 100, pointerId: 1 })
  fireEvent.pointerMove(element, { clientX: 100 + Math.sign(dx) * 10, pointerId: 1 })
  fireEvent.pointerMove(element, { clientX: 100 + dx, pointerId: 1 })
  fireEvent.pointerUp(element, { clientX: 100 + dx, pointerId: 1 })
}

describe('TrashPage — mobile handoff (M08f)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    vi.mocked(listProjects).mockResolvedValue([])
    mockGetTrash.mockResolvedValue(trash)
    mockRestoreProject.mockResolvedValue({ project: trash.projects[0], restored_task_count: 3 })
    mockRestoreTask.mockResolvedValue(trash.tasks[1])
    mockDeleteProject.mockResolvedValue()
    mockDeleteTask.mockResolvedValue()
    // jsdom has no <dialog>.showModal; the sheets need the two methods.
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute('open') }
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('renders the M08f tree: title row, group labels, rows with a two-fact meta line and foot rows', async () => {
    renderMobile()
    expect(await screen.findByRole('heading', { level: 1, name: 'Trash' })).toBeInTheDocument()
    expect(screen.getByText('3 items')).toBeInTheDocument()
    // Group labels carry the count beside them; the old section heads are gone.
    expect(screen.getByRole('region', { name: 'Projects' })).toBeInTheDocument()
    expect(screen.queryByText('Select all')).not.toBeInTheDocument()
    expect(screen.queryByText(/Restore all/)).not.toBeInTheDocument()
    // Meta: when, then what comes back — never the status word or the due date.
    const project = title('Old Wiki Rewrite').closest('.trash-row')!
    expect(within(project as HTMLElement).getByText(/3 tasks restore with it/)).toBeInTheDocument()
    const ups = title('Trial the second UPS unit').closest('.trash-row')!
    expect(within(ups as HTMLElement).getByText(/Deleted 2 days ago/)).toBeInTheDocument()
    expect(screen.queryByText(/Open/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Due/)).not.toBeInTheDocument()
    // A task whose project is also trashed names it.
    const script = title('Write the old wiki export script').closest('.trash-row')!
    expect(within(script as HTMLElement).getAllByText(/Old Wiki Rewrite/)).toHaveLength(1)
    // No destructive control at rest: Delete forever lives behind ⋯, Empty trash is a foot row.
    expect(screen.queryByRole('button', { name: /Delete .* forever/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Empty trash/ })).toHaveTextContent('Empty trash · 3')
    expect(screen.getByRole('button', { name: 'Select' })).toBeInTheDocument()
  })

  it('restores from the ring without a confirm and posts an undo that re-trashes the row', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const confirm = vi.spyOn(window, 'confirm')
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Restore project Old Wiki Rewrite' }))
    // Plain Restore on a project brings its archived tasks with it.
    await waitFor(() => expect(mockRestoreProject).toHaveBeenCalledWith(1, true))
    expect(confirm).not.toHaveBeenCalled()
    const undo = await screen.findByRole('status')
    expect(undo).toHaveTextContent('Restored · Old Wiki Rewrite')
    await user.click(within(undo).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(mockDeleteProject).toHaveBeenCalledWith(1))
    expect(await screen.findByText('Moved “Old Wiki Rewrite” back to the trash.')).toBeInTheDocument()
  })

  it('lets the undo bar expire after five seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Restore task Trial the second UPS unit' }))
    expect(await screen.findByText('Undo')).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(5000) })
    expect(screen.queryByText('Undo')).not.toBeInTheDocument()
    expect(mockDeleteTask).not.toHaveBeenCalled()
  })

  it('does not arm undo when the restore failed', async () => {
    mockRestoreTask.mockRejectedValueOnce(new Error('Restore failed'))
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Restore task Trial the second UPS unit' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Restore failed')
    expect(screen.queryByText('Undo')).not.toBeInTheDocument()
  })

  it('restores on a right swipe past the threshold and ignores a left swipe', async () => {
    renderMobile()
    const row = (await screen.findByText('Trial the second UPS unit')).closest('.trash-swipe-content')!
    swipe(row, -SWIPE_COMMIT_PX - 20)
    expect(mockRestoreTask).not.toHaveBeenCalled()
    swipe(row, SWIPE_COMMIT_PX - 10)
    expect(mockRestoreTask).not.toHaveBeenCalled()
    swipe(row, SWIPE_COMMIT_PX + 10)
    await waitFor(() => expect(mockRestoreTask).toHaveBeenCalledWith(6))
    expect(await screen.findByText('Undo')).toBeInTheDocument()
  })

  it('offers Restore, Restore without its tasks and a confirmed Delete forever in the ⋯ sheet', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockPurgeProject.mockResolvedValue()
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Actions for project Old Wiki Rewrite' }))
    const sheet = screen.getByRole('dialog', { name: 'Old Wiki Rewrite' })
    await user.click(within(sheet).getByRole('button', { name: 'Restore without its tasks' }))
    await waitFor(() => expect(mockRestoreProject).toHaveBeenCalledWith(1, false))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Actions for project Old Wiki Rewrite' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete forever' }))
    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete “Old Wiki Rewrite” and the 4 trashed tasks it owns? This cannot be undone.',
    )
    await waitFor(() => expect(mockPurgeProject).toHaveBeenCalledWith(1))
  })

  it('does not purge from the sheet when the confirm is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Actions for task Trial the second UPS unit' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete forever' }))
    expect(mockPurgeTask).not.toHaveBeenCalled()
  })

  it('filters through the pending sheet and shows the chips row whenever the list is filtered', async () => {
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Filter trash' }))
    const sheet = screen.getByRole('dialog', { name: 'Filters' })
    await user.click(within(sheet).getByRole('button', { name: 'Tasks' }))
    await user.type(within(sheet).getByLabelText('Search trash'), 'wiki')
    // Pending until Apply: the list behind is untouched.
    expect(title('Old Wiki Rewrite')).toBeInTheDocument()
    const apply = within(sheet).getByRole('button', { name: 'Show 1 item' })
    await user.click(apply)
    expect(queryTitle('Old Wiki Rewrite')).not.toBeInTheDocument()
    expect(queryTitle('Trial the second UPS unit')).not.toBeInTheDocument()
    expect(title('Write the old wiki export script')).toBeInTheDocument()
    const chips = screen.getByLabelText('Applied filters')
    expect(within(chips).getByRole('button', { name: 'Remove Tasks' })).toBeInTheDocument()
    expect(within(chips).getByRole('button', { name: 'Remove “wiki”' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Filter trash' })).toHaveClass('applied')
    // Removing one chip drops just that filter.
    await user.click(within(chips).getByRole('button', { name: 'Remove Tasks' }))
    expect(title('Old Wiki Rewrite')).toBeInTheDocument()
    await user.click(within(screen.getByLabelText('Applied filters')).getByRole('button', { name: 'Clear' }))
    expect(screen.queryByLabelText('Applied filters')).not.toBeInTheDocument()
  })

  it('shows the no-match state with a Clear action when the filters hide everything', async () => {
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Filter trash' }))
    await user.type(screen.getByLabelText('Search trash'), 'zzz')
    await user.click(screen.getByRole('button', { name: 'Show 0 items' }))
    const empty = screen.getByText('No items match your filters').parentElement!
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
    // The chips row stays above the no-match state, so the filter is still visible.
    expect(screen.getByLabelText('Applied filters')).toBeInTheDocument()
    await user.click(within(empty).getByRole('button', { name: 'Clear' }))
    expect(title('Old Wiki Rewrite')).toBeInTheDocument()
  })

  it('selection mode spans both kinds and fans one purge out to one server call', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockPurgeSelected.mockResolvedValue({ projects: 1, tasks: 5 })
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    expect(screen.getByText('0 selected')).toBeInTheDocument()
    // Rings, ⋯ and foot rows give way to checkboxes and the action bar.
    expect(screen.queryByRole('button', { name: /^Restore project/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Select project Old Wiki Rewrite' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select task Trial the second UPS unit' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    await user.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Delete forever' }))
    expect(confirm).toHaveBeenCalledWith(
      'Permanently delete 6 items (1 project, 1 task and 4 trashed tasks it owns)? This cannot be undone.',
    )
    await waitFor(() => expect(mockPurgeSelected).toHaveBeenCalledWith({ project_ids: [1], task_ids: [6] }))
    expect(await screen.findByText('Permanently deleted 6 items.')).toBeInTheDocument()
    // Exit clears the selection and the mode.
    expect(screen.getByRole('heading', { level: 1, name: 'Trash' })).toBeInTheDocument()
  })

  it('bulk-restores a selection with one combined notice and Select all covers both groups', async () => {
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('3 selected')).toBeInTheDocument()
    await user.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(mockRestoreTask).toHaveBeenCalledTimes(2))
    expect(mockRestoreProject).toHaveBeenCalledWith(1, true)
    expect(await screen.findByText('Restored 3 items. Brought back 3 tasks.')).toBeInTheDocument()
  })

  it('reports how far a half-failed bulk restore got', async () => {
    mockRestoreTask.mockRejectedValueOnce(new Error('Restore failed'))
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select task Write the old wiki export script' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select task Trial the second UPS unit' }))
    await user.click(within(screen.getByRole('toolbar')).getByRole('button', { name: 'Restore' }))
    expect(await screen.findByText('Restored 1 of 2 items.')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Restore failed')
  })

  it('exits selection mode with Done and clears the selection on a filter change', async () => {
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: 'Select' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select project Old Wiki Rewrite' }))
    await user.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.getByRole('button', { name: 'Select' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Select' }))
    expect(screen.getByText('0 selected')).toBeInTheDocument()
  })

  it('empties the trash from the foot row after the confirm', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockEmptyTrash.mockResolvedValue({ projects: 1, tasks: 6 })
    const user = userEvent.setup()
    renderMobile()
    await user.click(await screen.findByRole('button', { name: /Empty trash/ }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Permanently delete all 3 items in trash'))
    await waitFor(() => expect(mockEmptyTrash).toHaveBeenCalled())
  })

  it('renders skeleton rows while loading and the empty state without controls', async () => {
    let resolve: (value: Trash) => void = () => {}
    mockGetTrash.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    renderMobile()
    expect(screen.getByLabelText('Loading trash')).toBeInTheDocument()
    expect(screen.queryByText(/Loading trash…/)).not.toBeInTheDocument()
    act(() => resolve({ projects: [], tasks: [] }))
    expect(await screen.findByText('Nothing in the trash')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Trash' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filter trash' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Empty trash/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/items/)).not.toBeInTheDocument()
  })
})
