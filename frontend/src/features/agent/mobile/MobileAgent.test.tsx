import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  postMessage,
  renameConversation,
  restoreConversation,
} from '../../../api/agent'
import { deleteTask } from '../../../api/tasks'
import type { AgentMessage, Conversation, ConversationDetail, MessageExchange } from '../../../types/agent'
import { RequireRouteId } from '../../../routes/RequireRouteId'
import { AgentPage } from '../AgentPage'
import { SWIPE_COMMIT_PX } from './ConversationSwipeRow'
import { dismissDeleteUndo } from './deleteUndoStore'

/* M07f — the mobile tree, reached through AgentPage at phone width. */

vi.mock('../../../api/agent', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  postMessage: vi.fn(),
  renameConversation: vi.fn(),
  restoreConversation: vi.fn(),
}))
vi.mock('../../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
}))
vi.mock('../../../api/projects', () => ({ deleteProject: vi.fn(), restoreProject: vi.fn() }))

const mockList = vi.mocked(listConversations)
const mockCreate = vi.mocked(createConversation)
const mockDelete = vi.mocked(deleteConversation)
const mockRestore = vi.mocked(restoreConversation)
const mockRename = vi.mocked(renameConversation)
const mockGet = vi.mocked(getConversation)
const mockPost = vi.mocked(postMessage)
const mockDeleteTask = vi.mocked(deleteTask)

function message(overrides: Partial<AgentMessage>): AgentMessage {
  return {
    id: 1,
    conversation_id: 8,
    role: 'user',
    content: 'hello',
    tool_calls: null,
    stop_reason: null,
    created_at: '2026-09-10T10:00:00Z',
    ...overrides,
  }
}

const reply = message({
  id: 2,
  role: 'assistant',
  content: 'Two tasks are overdue:\n\n- Decommission the old NAS array\n- Patch the rack switch firmware',
  stop_reason: 'completed',
  tool_calls: [
    { tool: 'list_tasks', arguments: {}, result: '[]', error: null },
    { tool: 'get_task', arguments: { task_id: 4 }, result: JSON.stringify({ id: 4, title: 'Decommission the old NAS array' }), error: null },
    { tool: 'search', arguments: { query: 'firmware' }, result: '{}', error: null },
    {
      tool: 'create_task',
      arguments: { data: { title: 'Draft the migration comms' } },
      result: JSON.stringify({ id: 7, title: 'Draft the migration comms' }),
      error: null,
    },
    { tool: 'update_task', arguments: { task_id: 4, data: { scheduled_for: 'x' } }, result: null, error: 'unknown field "scheduled_for"' },
  ],
})

const conversations: Conversation[] = [
  { id: 8, title: "What's overdue in Homelab?", created_at: '2026-09-10T09:00:00Z', updated_at: '2026-09-10T10:00:00Z' },
  { id: 7, title: 'Plan my Thursday', created_at: '2026-09-09T09:00:00Z', updated_at: '2026-09-09T10:00:00Z' },
  { id: 6, title: 'Weekly review', created_at: '2026-09-04T09:00:00Z', updated_at: '2026-09-04T10:00:00Z' },
]

const detail: ConversationDetail = {
  ...conversations[0],
  messages: [message({ content: "What's overdue in Homelab Migration?" }), reply],
  message_count: 2,
  has_more: false,
}

function Location() {
  const location = useLocation()
  return <span data-testid="location">{location.pathname}</span>
}

/** The app's own route shape (AppRoutes): the id route is wrapped, so going
 * from `/agent/:id` to `/agent` remounts the page — the case that lost the
 * undo bar in the browser. */
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agent" element={<AgentPage />} />
        <Route
          path="/agent/:conversationId"
          element={
            <RequireRouteId param="conversationId">
              <AgentPage />
            </RequireRouteId>
          }
        />
      </Routes>
      <Location />
    </MemoryRouter>,
  )
}

/** A pointer drag on a row's content: press, one move past the slop, one to `dx`, release. */
function swipe(element: Element, dx: number): void {
  fireEvent.pointerDown(element, { button: 0, clientX: 200, pointerId: 1 })
  fireEvent.pointerMove(element, { clientX: 200 + Math.sign(dx) * 10, pointerId: 1 })
  fireEvent.pointerMove(element, { clientX: 200 + dx, pointerId: 1 })
  fireEvent.pointerUp(element, { clientX: 200 + dx, pointerId: 1 })
}

/** The swipeable content of the conversations-sheet row titled `title`. */
function rowContent(title: string): HTMLElement {
  return screen.getByText(title, { selector: '.magent-row-title' }).closest<HTMLElement>('.magent-swipe-content')!
}

async function openConversationsSheet() {
  await userEvent.click(screen.getByRole('button', { name: 'Switch conversation' }))
  return screen.getByRole('dialog', { name: 'Conversations' })
}

describe('AgentPage — mobile handoff (M07f)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    dismissDeleteUndo()
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    mockList.mockResolvedValue(conversations)
    mockGet.mockResolvedValue(detail)
    mockDelete.mockResolvedValue()
    mockRestore.mockResolvedValue(conversations[1])
    mockRename.mockImplementation(async (id, title) => ({ ...conversations[0], id, title }))
    mockCreate.mockResolvedValue({ id: 99, title: null, created_at: detail.created_at, updated_at: detail.updated_at })
    // jsdom has no <dialog>.showModal; the sheets need the two methods.
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute('open') }
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('replaces the rail with a title row and gives the agent the page', async () => {
    renderAt('/agent/8')
    await screen.findByText("What's overdue in Homelab Migration?")

    // No sidebar, no desktop composer controls.
    expect(screen.queryByRole('complementary', { name: 'Conversations' })).toBeNull()
    expect(screen.queryByRole('button', { name: /spoken replies/i })).toBeNull()

    const title = screen.getByRole('button', { name: 'Switch conversation' })
    expect(title).toHaveTextContent("What's overdue in Homelab?")
    expect(screen.getByRole('button', { name: 'Conversation actions' })).toBeInTheDocument()

    // The reply is a body, not a bubble; the user keeps the bubble.
    const agentTurn = screen.getByText('Two tasks are overdue:').closest<HTMLElement>('.magent-turn')!
    expect(agentTurn).toHaveClass('magent-turn--agent')
    expect(agentTurn.querySelector('.magent-bubble')).toBeNull()
    expect(agentTurn.querySelector('.magent-body')).not.toBeNull()
    expect(screen.getByText("What's overdue in Homelab Migration?")).toHaveClass('magent-bubble')
  })

  it('collapses reads into one row, lists mutations with a 44px undo, keeps failures struck through', async () => {
    mockDeleteTask.mockResolvedValue()
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    // Three successful reads → one row; the detail is a tap away.
    const reads = screen.getByRole('button', { name: 'Read 3 things' })
    expect(reads).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Looked at tasks')).toBeNull()
    await userEvent.click(reads)
    expect(screen.getByText('Looked at tasks')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Looked at a task' })).toHaveAttribute('href', '/tasks/4')
    expect(screen.getByText('Searched for “firmware”')).toBeInTheDocument()

    // The mutation keeps its row and a short Undo whose full label is the title.
    const undo = screen.getByRole('button', { name: /Undo \(move to trash\): Created task/ })
    expect(undo).toHaveTextContent('Undo')
    expect(undo).toHaveAttribute('title', 'Undo (move to trash)')
    expect(screen.getByRole('link', { name: 'Created task “Draft the migration comms”' })).toHaveAttribute('href', '/tasks/7')

    // The failed call stays visible with its error, and offers no undo.
    const failed = screen.getByText('Updated task').closest<HTMLElement>('.magent-call')!
    expect(failed).toHaveClass('magent-call--failed')
    expect(within(failed).getByText(/unknown field/)).toBeInTheDocument()
    expect(within(failed).queryByRole('button')).toBeNull()

    await userEvent.click(undo)
    await waitFor(() => expect(mockDeleteTask).toHaveBeenCalledWith(7))
    expect(screen.getByText('Undone')).toBeInTheDocument()
    // An undone create now lives in the trash — the link follows it.
    expect(screen.getByRole('link', { name: 'Created task “Draft the migration comms”' })).toHaveAttribute('href', '/trash')
  })

  it('shares one composer slot between send and mic, and shows an elapsed clock while the run is in flight', async () => {
    let land: (exchange: MessageExchange) => void = () => {}
    mockPost.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    // No MediaRecorder in jsdom: the slot falls back to a disabled send.
    const send = screen.getByRole('button', { name: 'Send message' })
    expect(send).toBeDisabled()

    const composer = screen.getByRole('textbox', { name: 'Message the agent' })
    expect(composer).toHaveAttribute('rows', '1')
    await userEvent.type(composer, 'Split the relaunch epic')
    expect(screen.getByRole('button', { name: 'Send message' })).toBeEnabled()

    await userEvent.keyboard('{Enter}')
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(8, 'Split the relaunch epic'))

    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Working — reading your projects')
    expect(status).toHaveTextContent('0:00')
    expect(status).not.toHaveTextContent('First run today can take a minute')
    // Delete is off for the running conversation — the server would 409 it.
    await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
    expect(screen.getByRole('button', { name: 'Delete conversation' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    const user = message({ id: 3, content: 'Split the relaunch epic' })
    const assistant = message({ id: 4, role: 'assistant', content: 'Done.', stop_reason: 'completed' })
    mockGet.mockResolvedValue({ ...detail, messages: [...detail.messages, user, assistant], message_count: 4 })
    await act(async () => { land({ user_message: user, assistant_message: assistant }) })
    await screen.findByText('Done.')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('ticks the clock once a second and adds the cold-load note past five seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockPost.mockImplementation(() => new Promise(() => {}))
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    const composer = screen.getByRole('textbox', { name: 'Message the agent' })
    fireEvent.change(composer, { target: { value: 'Plan my Thursday' } })
    fireEvent.submit(composer.closest('form')!)
    await screen.findByRole('status')

    await act(async () => { await vi.advanceTimersByTimeAsync(6_100) })
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('0:06')
    expect(status).toHaveTextContent('First run today can take a minute')
  })

  it('opens the conversations sheet from the title row, and a left swipe deletes with a five-second undo', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    const sheet = await openConversationsSheet()
    expect(within(sheet).getByRole('button', { name: 'New conversation' })).toBeInTheDocument()
    expect(within(sheet).getByText('Swipe a conversation left to delete it. Long-press for the same actions.')).toBeInTheDocument()
    // The active row is marked.
    expect(within(sheet).getByRole('button', { name: /What's overdue in Homelab\?/ })).toHaveAttribute('aria-current', 'true')

    // Right drag is clamped to zero — nothing happens.
    swipe(rowContent('Plan my Thursday'), SWIPE_COMMIT_PX + 20)
    expect(mockDelete).not.toHaveBeenCalled()
    // Under the threshold: snap back, no delete.
    swipe(rowContent('Plan my Thursday'), -(SWIPE_COMMIT_PX - 10))
    expect(mockDelete).not.toHaveBeenCalled()

    swipe(rowContent('Plan my Thursday'), -(SWIPE_COMMIT_PX + 4))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(7))
    // The sheet closes so the undo bar is reachable; the thread stays put.
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByTestId('location')).toHaveTextContent('/agent/8')
    const undo = screen.getByRole('status')
    expect(undo).toHaveTextContent('Deleted · Plan my Thursday')

    await userEvent.click(within(undo).getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(mockRestore).toHaveBeenCalledWith(7))
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('expires the undo bar after five seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')
    await openConversationsSheet()
    swipe(rowContent('Weekly review'), -SWIPE_COMMIT_PX)
    await screen.findByText('Deleted · Weekly review')

    await act(async () => { await vi.advanceTimersByTimeAsync(5_100) })
    expect(screen.queryByText('Deleted · Weekly review')).toBeNull()
    expect(mockRestore).not.toHaveBeenCalled()
  })

  it('deleting the active conversation from ⋯ navigates to /agent, and undo navigates back', async () => {
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
    const sheet = screen.getByRole('dialog', { name: "What's overdue in Homelab?" })
    expect(within(sheet).getByRole('button', { name: 'Rename' })).toBeInTheDocument()
    await userEvent.click(within(sheet).getByRole('button', { name: 'Delete conversation' }))

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(8))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/agent$/))
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()
    expect(screen.getByText("Deleted · What's overdue in Homelab?")).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    await waitFor(() => expect(mockRestore).toHaveBeenCalledWith(8))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/agent/8'))
    await screen.findByText('Two tasks are overdue:')
  })

  it('long-press opens the actions sheet for that row, and spoken replies is a device preference there', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')
    await openConversationsSheet()

    const content = rowContent('Weekly review')
    fireEvent.pointerDown(content, { button: 0, clientX: 200, pointerId: 1 })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    fireEvent.pointerUp(content, { clientX: 200, pointerId: 1 })

    const sheet = await screen.findByRole('dialog', { name: 'Weekly review' })
    const voice = within(sheet).getByRole('switch', { name: /Spoken replies/ })
    expect(voice).toHaveAttribute('aria-checked', 'true')
    expect(voice).toHaveTextContent('On')
    await userEvent.click(voice)
    expect(voice).toHaveAttribute('aria-checked', 'false')
    expect(voice).toHaveTextContent('Off')
    expect(localStorage.getItem('agent-voice-output')).toBe('off')

    // Delete from here targets the long-pressed row, not the active one.
    await userEvent.click(within(sheet).getByRole('button', { name: 'Delete conversation' }))
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(6))
    expect(screen.getByTestId('location')).toHaveTextContent('/agent/8')
  })

  it('renames through a sheet, not window.prompt', async () => {
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')
    await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
    await userEvent.click(screen.getByRole('button', { name: 'Rename' }))

    const field = screen.getByRole('textbox', { name: 'Conversation title' })
    expect(field).toHaveValue("What's overdue in Homelab?")
    await userEvent.clear(field)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    await userEvent.type(field, 'Homelab overdue{Enter}')
    await waitFor(() => expect(mockRename).toHaveBeenCalledWith(8, 'Homelab overdue'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('shows the empty state at /agent with a recents button into the sheet', async () => {
    renderAt('/agent')
    await screen.findByRole('heading', { name: 'Agent' })
    expect(screen.queryByRole('button', { name: 'Switch conversation' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: '3 recent conversations' }))
    const sheet = screen.getByRole('dialog', { name: 'Conversations' })
    await userEvent.click(within(sheet).getByRole('button', { name: /Plan my Thursday/ }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/agent/7'))

    // Start a conversation creates and navigates.
    await userEvent.click(screen.getByRole('button', { name: 'Switch conversation' }))
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    await waitFor(() => expect(mockCreate).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/agent/99'))
  })

  it('surfaces a failed delete and keeps the conversation', async () => {
    mockDelete.mockRejectedValue(new Error('API error 409'))
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')
    await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
    await userEvent.click(screen.getByRole('button', { name: 'Delete conversation' }))

    await screen.findByRole('alert')
    expect(screen.getByRole('alert')).toHaveTextContent('API error 409')
    expect(screen.getByTestId('location')).toHaveTextContent('/agent/8')
    expect(screen.queryByText(/^Deleted ·/)).toBeNull()
  })
})
