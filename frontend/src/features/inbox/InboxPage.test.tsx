import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  dismissInbox,
  getCandidates,
  listPendingInbox,
  reviewInbox,
} from '../../api/inbox'
import { listProjects } from '../../api/projects'
import type { InboxItem, ReviewRequest } from '../../types/inbox'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { InboxPage } from './InboxPage'

vi.mock('../../api/inbox', () => ({
  createInbox: vi.fn(),
  dismissInbox: vi.fn(),
  getCandidates: vi.fn(),
  getInbox: vi.fn(),
  listInbox: vi.fn(),
  listPendingInbox: vi.fn(),
  processInbox: vi.fn(),
  reviewInbox: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
}))

const mockDismissInbox = vi.mocked(dismissInbox)
const mockGetCandidates = vi.mocked(getCandidates)
const mockListPendingInbox = vi.mocked(listPendingInbox)
const mockListProjects = vi.mocked(listProjects)
const mockReviewInbox = vi.mocked(reviewInbox)

const pendingItem: InboxItem = {
  id: 101,
  raw_text: 'Kickoff needs a brief and expenses should be filed.',
  input_hash: 'pending-hash',
  source: 'discord',
  summary: 'Kickoff and expenses',
  project_hint: 'Launch',
  needs_review: true,
  processed_at: '2026-06-01T17:00:00Z',
  reviewed_at: null,
  model_name: 'test-model',
  suggested_project_id: 2,
  created_at: '2026-06-01T17:00:00Z',
  updated_at: '2026-06-01T17:00:00Z',
}

const projects: Project[] = [
  {
    id: 1,
    name: 'General',
    description: null,
    system_key: 'general',
    is_protected: true,
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
  },
  {
    id: 2,
    name: 'Launch',
    description: null,
    system_key: null,
    is_protected: false,
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
  },
]

const candidates: Task[] = [
  {
    id: 201,
    project_id: null,
    inbox_item_id: pendingItem.id,
    parent_task_id: null,
    estimated_minutes: null,
    is_blocked: false,
    title: 'Draft kickoff brief',
    description: 'Prepare launch kickoff notes',
    status: 'candidate',
    priority: 'medium',
    due_date: '2026-06-05',
    confidence: 0.92,
    assignee_hint: null,
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
  },
  {
    id: 202,
    project_id: null,
    inbox_item_id: pendingItem.id,
    parent_task_id: null,
    estimated_minutes: null,
    is_blocked: false,
    title: 'File expense report',
    description: null,
    status: 'candidate',
    priority: 'low',
    due_date: null,
    confidence: 0.74,
    assignee_hint: 'Alex',
    created_at: '2026-06-01T17:00:00Z',
    updated_at: '2026-06-01T17:00:00Z',
  },
]

describe('InboxPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListPendingInbox
      .mockResolvedValueOnce([pendingItem])
      .mockResolvedValueOnce([])
    mockListProjects.mockResolvedValue(projects)
    mockGetCandidates.mockResolvedValue(candidates)
    mockDismissInbox.mockResolvedValue(undefined)
    mockReviewInbox.mockResolvedValue({
      accepted: 1,
      rejected: 1,
      training_example_id: 301,
      match_training_example_id: 302,
    })
  })

  it('reviews a pending inbox item with edits and a rejection', async () => {
    const user = userEvent.setup()

    render(<InboxPage />)

    expect(
      await screen.findByRole('heading', { name: 'Awaiting review (1)' }),
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: /\[discord\] Kickoff and expenses/ }),
    )

    expect(mockGetCandidates).toHaveBeenCalledWith(pendingItem.id)
    expect(
      await screen.findByRole('heading', { name: 'Review candidates (2)' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Matched project:').closest('p')).toHaveTextContent(
      'Launch',
    )

    await user.clear(
      screen.getByRole('textbox', { name: 'Title for Draft kickoff brief' }),
    )
    await user.type(
      screen.getByRole('textbox', { name: 'Title for Draft kickoff brief' }),
      'Draft launch kickoff brief',
    )
    await user.selectOptions(
      screen.getByRole('combobox', {
        name: 'Priority for Draft kickoff brief',
      }),
      'high',
    )
    await user.selectOptions(
      screen.getByRole('combobox', {
        name: 'Project for Draft kickoff brief',
      }),
      '1',
    )

    await user.click(
      screen.getByRole('checkbox', {
        name: 'Review action for File expense report',
      }),
    )

    const rejectedRow = screen
      .getByRole('textbox', { name: 'Title for File expense report' })
      .closest('li')
    expect(rejectedRow).not.toBeNull()
    expect(
      within(rejectedRow as HTMLElement).getByText('Reject'),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Submit review' }))

    const expectedReview: ReviewRequest = {
      decisions: [
        {
          task_id: 201,
          action: 'accept',
          edits: {
            title: 'Draft launch kickoff brief',
            priority: 'high',
            project_id: 1,
          },
        },
        { task_id: 202, action: 'reject' },
      ],
    }
    expect(mockReviewInbox).toHaveBeenCalledWith(pendingItem.id, expectedReview)
    expect(
      await screen.findByRole('status'),
    ).toHaveTextContent('Review saved — 1 accepted, 1 rejected.')
    expect(
      screen.queryByRole('heading', { name: 'Review candidates (2)' }),
    ).not.toBeInTheDocument()
    expect(mockListPendingInbox).toHaveBeenCalledTimes(2)
  })

  it('dismisses a pending inbox item from the queue', async () => {
    const user = userEvent.setup()

    render(<InboxPage />)

    expect(
      await screen.findByRole('heading', { name: 'Awaiting review (1)' }),
    ).toBeInTheDocument()

    await user.click(
      screen.getByRole('button', { name: /Dismiss Kickoff and expenses/ }),
    )

    expect(mockDismissInbox).toHaveBeenCalledWith(pendingItem.id)
    // The queue reloads (now empty) and the item drops out of the list.
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: 'Awaiting review (1)' }),
      ).not.toBeInTheDocument(),
    )
    expect(mockListPendingInbox).toHaveBeenCalledTimes(2)
  })
})
