import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { decideCandidate, dismissInbox, getCandidates, getInbox, listPendingInbox } from '../../api/inbox'
import type { InboxItem } from '../../types/inbox'
import type { Task } from '../../types/task'
import { InboxPage } from './InboxPage'

vi.mock('../../api/inbox', () => ({
  createInbox: vi.fn(),
  decideCandidate: vi.fn(),
  dismissInbox: vi.fn(),
  getCandidates: vi.fn(),
  getInbox: vi.fn(),
  listInbox: vi.fn(),
  listPendingInbox: vi.fn(),
  processInbox: vi.fn(),
  reviewInbox: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn().mockResolvedValue([]),
}))

const mockListPendingInbox = vi.mocked(listPendingInbox)
const mockGetCandidates = vi.mocked(getCandidates)
const mockGetInbox = vi.mocked(getInbox)
const mockDecideCandidate = vi.mocked(decideCandidate)
const mockDismissInbox = vi.mocked(dismissInbox)

const pendingItem: InboxItem = {
  id: 10,
  raw_text: 'Some notes',
  input_hash: 'abc',
  source: 'web',
  summary: 'Sprint notes',
  project_hint: null,
  needs_review: true,
  processed_at: '2026-06-01T10:00:00Z',
  reviewed_at: null,
  model_name: null,
  suggested_project_id: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
}

const candidate: Task = {
  id: 201,
  project_id: null,
  inbox_item_id: 10,
  parent_task_id: null,
  title: 'Fix the router',
  description: null,
  review_status: 'candidate',
  workflow_status: 'open',
  priority: 'medium',
  due_date: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  confidence: 0.9,
  assignee_hint: null,
  created_at: '2026-06-01T10:00:00Z',
  updated_at: '2026-06-01T10:00:00Z',
  is_blocked: false,
  is_blocking: false,
  blocked_task_count: 0,
  has_subtasks: false,
}

const candidate2: Task = { ...candidate, id: 202, title: 'Update firmware' }

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/inbox']}>
      <Routes>
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/inbox/:inboxId" element={<InboxPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('InboxPage', () => {
  afterEach(cleanup)

  beforeEach(() => {
    vi.clearAllMocks()
    mockDismissInbox.mockResolvedValue(undefined)
    // Selecting a note navigates to /inbox/:id, which loads the item by id.
    mockGetInbox.mockResolvedValue(pendingItem)
  })

  it('shows pending note when present', async () => {
    mockListPendingInbox.mockResolvedValue([pendingItem])
    renderPage()
    expect(await screen.findByText('Sprint notes')).toBeInTheDocument()
  })

  it('shows empty state when no pending notes', async () => {
    mockListPendingInbox.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No notes awaiting review.')).toBeInTheDocument()
  })

  it('renders editable candidate cards on note click', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem])
    mockGetCandidates.mockResolvedValue([candidate, candidate2])
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))

    expect(await screen.findByDisplayValue('Fix the router')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Update firmware')).toBeInTheDocument()
  })

  it('dismiss removes a single candidate from the list', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem])
    mockGetCandidates.mockResolvedValue([candidate, candidate2])
    mockDecideCandidate.mockResolvedValue({
      task_id: candidate.id,
      action: 'dismissed',
      finalized: false,
      training_example_id: null,
      match_training_example_id: null,
    })
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))
    await screen.findByDisplayValue('Fix the router')

    const dismissButtons = screen.getAllByRole('button', { name: 'Dismiss' })
    await user.click(dismissButtons[0])

    expect(mockDecideCandidate).toHaveBeenCalledWith(10, candidate.id, { action: 'dismiss' })
    expect(screen.queryByDisplayValue('Fix the router')).not.toBeInTheDocument()
    expect(screen.getByDisplayValue('Update firmware')).toBeInTheDocument()
  })

  it('approve sends in-place edits with the decision', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem])
    mockGetCandidates.mockResolvedValue([candidate])
    mockDecideCandidate.mockResolvedValue({
      task_id: candidate.id,
      action: 'approved',
      finalized: true,
      training_example_id: 99,
      match_training_example_id: null,
    })
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))
    const title = await screen.findByDisplayValue('Fix the router')

    await user.clear(title)
    await user.type(title, 'Fix the core router')
    await user.click(screen.getByRole('button', { name: 'Priority: medium' }))
    await user.click(screen.getByRole('button', { name: 'high' }))
    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(mockDecideCandidate).toHaveBeenCalledWith(10, candidate.id, {
      action: 'approve',
      edits: { title: 'Fix the core router', priority: 'high' },
    })
  })

  it('approve without edits sends no edits payload', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem])
    mockGetCandidates.mockResolvedValue([candidate])
    mockDecideCandidate.mockResolvedValue({
      task_id: candidate.id,
      action: 'approved',
      finalized: true,
      training_example_id: 99,
      match_training_example_id: null,
    })
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))
    await screen.findByDisplayValue('Fix the router')

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(mockDecideCandidate).toHaveBeenCalledWith(10, candidate.id, { action: 'approve' })
  })

  it('deciding a candidate advances focus to the next card', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem])
    // candidate2 has lower confidence, so it sorts first and gets decided first.
    mockGetCandidates.mockResolvedValue([candidate, { ...candidate2, confidence: 0.4 }])
    mockDecideCandidate.mockResolvedValue({
      task_id: candidate2.id,
      action: 'dismissed',
      finalized: false,
      training_example_id: null,
      match_training_example_id: null,
    })
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))
    await screen.findByDisplayValue('Update firmware')

    await user.click(screen.getAllByRole('button', { name: 'Dismiss' })[0])

    expect(screen.getByDisplayValue('Fix the router')).toHaveFocus()
  })

  it('approving the last candidate shows finalized notice', async () => {
    const user = userEvent.setup()
    mockListPendingInbox.mockResolvedValue([pendingItem]).mockResolvedValueOnce([pendingItem]).mockResolvedValue([])
    mockGetCandidates.mockResolvedValue([candidate])
    mockDecideCandidate.mockResolvedValue({
      task_id: candidate.id,
      action: 'approved',
      finalized: true,
      training_example_id: 99,
      match_training_example_id: null,
    })
    renderPage()

    await user.click(await screen.findByText('Sprint notes'))
    await screen.findByDisplayValue('Fix the router')

    await user.click(screen.getByRole('button', { name: 'Approve' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Note finalized')
  })
})
