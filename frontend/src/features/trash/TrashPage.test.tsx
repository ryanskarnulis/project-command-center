import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { restoreInbox } from '../../api/inbox'
import { listProjects, purgeProject, restoreProject } from '../../api/projects'
import { purgeTask, restoreTask } from '../../api/tasks'
import { emptyTrash, getTrash } from '../../api/trash'
import type { Trash } from '../../types/trash'
import { TrashPage } from './TrashPage'

const renderPage = () => render(<TrashPage />, { wrapper: MemoryRouter })

// Relative to the test run's clock so formatRelative is deterministic ("3 days ago").
const DELETED_AT = new Date(Date.now() - 3 * 86_400_000).toISOString()

vi.mock('../../api/trash', () => ({ getTrash: vi.fn(), emptyTrash: vi.fn() }))
vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
  restoreProject: vi.fn(),
  purgeProject: vi.fn(),
}))
vi.mock('../../api/tasks', () => ({ restoreTask: vi.fn(), purgeTask: vi.fn() }))
vi.mock('../../api/inbox', () => ({ restoreInbox: vi.fn(), purgeInbox: vi.fn() }))

const mockGetTrash = vi.mocked(getTrash)
const mockEmptyTrash = vi.mocked(emptyTrash)
const mockRestoreProject = vi.mocked(restoreProject)
const mockListProjects = vi.mocked(listProjects)
const mockRestoreTask = vi.mocked(restoreTask)
const mockRestoreInbox = vi.mocked(restoreInbox)
const mockPurgeProject = vi.mocked(purgeProject)
const mockPurgeTask = vi.mocked(purgeTask)

const trash: Trash = {
  projects: [
    {
      id: 1,
      name: 'Firewall',
      description: null,
      system_key: null,
      is_protected: false,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
      archived_task_count: 0,
    },
  ],
  tasks: [
    {
      id: 5,
      project_id: null,
      inbox_item_id: null,
      parent_task_id: null,
      estimated_minutes: null,
      repeat_interval: null,
      recurrence_id: null,
      is_blocked: false,
      is_blocking: false,
      blocked_task_count: 0,
      has_subtasks: false,
      title: 'Pay invoice',
      description: null,
      review_status: 'accepted',
      workflow_status: 'open',
      priority: 'medium',
      due_date: null,
      deferred_until: null,
      confidence: null,
      assignee_hint: null,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
    },
  ],
  inbox_items: [
    {
      id: 9,
      raw_text: 'dismissed note',
      input_hash: 'h',
      source: 'web',
      summary: 'A dismissed note',
      project_hint: null,
      needs_review: true,
      processed_at: '2026-06-01T17:00:00Z',
      reviewed_at: null,
      model_name: null,
      suggested_project_id: null,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
      deleted_at: DELETED_AT,
    },
  ],
  training_examples: [
    {
      id: 13,
      task_name: 'task_extraction',
      input_text: 'junk note to prune',
      model_output_json: '{"tasks": []}',
      corrected_output_json: null,
      accepted: false,
      model_profile: 'task_extraction',
      model_name: 'gemma4:e2b',
      created_at: '2026-06-01T17:00:00Z',
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
      .mockResolvedValue({ projects: [], tasks: [], inbox_items: [], training_examples: [] })
  })

  it('lists deleted items and restores a project', async () => {
    const user = userEvent.setup()
    mockRestoreProject.mockResolvedValue({ project: trash.projects[0], restored_task_count: 0 })

    renderPage()

    expect(await screen.findByText('Firewall')).toBeInTheDocument()
    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    expect(screen.getByText(/A dismissed note/)).toBeInTheDocument()
    expect(screen.getByText('junk note to prune')).toBeInTheDocument()
    // Each card shows a relative deleted-time label (fixtures deleted 3 days ago).
    expect(screen.getAllByText('Deleted 3 days ago').length).toBe(4)

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
      .mockResolvedValue({ projects: [], tasks: [], inbox_items: [], training_examples: [] })
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

  it('exposes restore handlers for tasks and inbox items', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])
    mockRestoreInbox.mockResolvedValue(trash.inbox_items[0])

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
    mockGetTrash.mockResolvedValue({ projects: [], tasks: [], inbox_items: [], training_examples: [] })

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
    expect(screen.queryByText(/A dismissed note/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear' }))

    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    expect(screen.getByText(/A dismissed note/)).toBeInTheDocument()
  })

  it('isolates a single section with the type filter', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')

    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    expect(screen.queryByText('Firewall')).not.toBeInTheDocument()
    expect(screen.queryByText(/A dismissed note/)).not.toBeInTheDocument()
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

  it('restores a whole section with Restore all', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])

    renderPage()

    // Isolate the Tasks section so there's a single "Restore all" button.
    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'tasks')
    await user.click(screen.getByRole('button', { name: 'Restore all' }))

    expect(mockRestoreTask).toHaveBeenCalledWith(5)
    expect(await screen.findByRole('status')).toHaveTextContent(/Restored 1 task./)
  })

  it('messages the inbox 409 when a Restore all hits a re-captured note', async () => {
    const user = userEvent.setup()
    mockRestoreInbox.mockRejectedValue(new ApiError(409, {}))

    renderPage()

    await screen.findByText('Firewall')
    await user.selectOptions(screen.getByLabelText('Filter by type'), 'inbox')
    await user.click(screen.getByRole('button', { name: 'Restore all' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/re-captured/)
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

  it('empties the whole trash after the user confirms', async () => {
    const user = userEvent.setup()
    mockEmptyTrash.mockResolvedValue({ projects: 1, tasks: 1, inbox_items: 1, training_examples: 0 })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Empty trash' }))

    expect(confirm).toHaveBeenCalled()
    expect(mockEmptyTrash).toHaveBeenCalled()
    expect(await screen.findByRole('status')).toHaveTextContent(
      /Permanently deleted 3 items/,
    )
    await waitFor(() =>
      expect(screen.getByText('Trash is empty.')).toBeInTheDocument(),
    )
    confirm.mockRestore()
  })
})
