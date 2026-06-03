import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { restoreInbox } from '../../api/inbox'
import { restoreProject } from '../../api/projects'
import { restoreTask } from '../../api/tasks'
import { getTrash } from '../../api/trash'
import type { Trash } from '../../types/trash'
import { TrashPage } from './TrashPage'

vi.mock('../../api/trash', () => ({ getTrash: vi.fn() }))
vi.mock('../../api/projects', () => ({ restoreProject: vi.fn() }))
vi.mock('../../api/tasks', () => ({ restoreTask: vi.fn() }))
vi.mock('../../api/inbox', () => ({ restoreInbox: vi.fn() }))

const mockGetTrash = vi.mocked(getTrash)
const mockRestoreProject = vi.mocked(restoreProject)
const mockRestoreTask = vi.mocked(restoreTask)
const mockRestoreInbox = vi.mocked(restoreInbox)

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
    },
  ],
  tasks: [
    {
      id: 5,
      project_id: null,
      inbox_item_id: null,
      parent_task_id: null,
      estimated_minutes: null,
      title: 'Pay invoice',
      description: null,
      status: 'accepted',
      priority: 'medium',
      due_date: null,
      confidence: null,
      assignee_hint: null,
      created_at: '2026-06-01T17:00:00Z',
      updated_at: '2026-06-01T17:00:00Z',
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
    },
  ],
}

describe('TrashPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // First load shows the deleted items; the post-restore reload is empty.
    mockGetTrash
      .mockResolvedValueOnce(trash)
      .mockResolvedValue({ projects: [], tasks: [], inbox_items: [] })
  })

  it('lists deleted items and restores a project', async () => {
    const user = userEvent.setup()
    mockRestoreProject.mockResolvedValue(trash.projects[0])

    render(<TrashPage />)

    expect(await screen.findByText('Firewall')).toBeInTheDocument()
    expect(screen.getByText('Pay invoice')).toBeInTheDocument()
    expect(screen.getByText(/A dismissed note/)).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: 'Restore project Firewall' }),
    )

    expect(mockRestoreProject).toHaveBeenCalledWith(1)
    await waitFor(() =>
      expect(screen.getByText('Trash is empty.')).toBeInTheDocument(),
    )
  })

  it('exposes restore handlers for tasks and inbox items', async () => {
    const user = userEvent.setup()
    mockRestoreTask.mockResolvedValue(trash.tasks[0])
    mockRestoreInbox.mockResolvedValue(trash.inbox_items[0])

    render(<TrashPage />)

    await user.click(
      await screen.findByRole('button', { name: 'Restore task Pay invoice' }),
    )
    expect(mockRestoreTask).toHaveBeenCalledWith(5)
  })
})
