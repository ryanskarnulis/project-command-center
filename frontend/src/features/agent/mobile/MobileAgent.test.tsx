import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelRun,
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
import { dismissDeleteUndo } from '../deleteUndoStore'
import { resetInFlightRuns } from '../inFlightRunStore'

/* M07f — the mobile tree, reached through AgentPage at phone width. */

vi.mock('../../../api/agent', () => ({
  cancelRun: vi.fn(),
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
/* The vendored mic is its own module with its own tests; here only whether a
   tap reaches it matters. Rendered only where MediaRecorder exists (#326). */
const micTap = vi.hoisted(() => vi.fn())
vi.mock('../../../voice/MicButton', () => ({
  MicButton: () => (
    <button type="button" className="mic-button mic-idle" aria-label="Start voice conversation" onClick={micTap}>
      mic
    </button>
  ),
}))

const mockList = vi.mocked(listConversations)
const mockCreate = vi.mocked(createConversation)
const mockDelete = vi.mocked(deleteConversation)
const mockRestore = vi.mocked(restoreConversation)
const mockRename = vi.mocked(renameConversation)
const mockGet = vi.mocked(getConversation)
const mockPost = vi.mocked(postMessage)
const mockCancel = vi.mocked(cancelRun)
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
      arguments: { title: 'Draft the migration comms' },
      result: JSON.stringify({ id: 7, title: 'Draft the migration comms' }),
      error: null,
    },
    { tool: 'update_task', arguments: { task_id: 4, scheduled_for: 'x' }, result: null, error: 'unknown field "scheduled_for"' },
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
  run_in_flight: false,
  run_started_at: null,
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
    // A test that leaves a send unresolved must not leak its run (#353).
    resetInFlightRuns()
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

  it('offers Stop beside the clock, and shows the truthful cancelled turn when the run lands', async () => {
    let land: (exchange: MessageExchange) => void = () => {}
    mockPost.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    mockCancel.mockResolvedValue({ cancel_requested: true })
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    const composer = screen.getByRole('textbox', { name: 'Message the agent' })
    fireEvent.change(composer, { target: { value: 'Make three tasks' } })
    fireEvent.submit(composer.closest('form')!)
    const status = await screen.findByRole('status')

    // Stop sits in the working block, next to the m:ss clock.
    const stop = within(status).getByRole('button', { name: 'Stop' })
    expect(stop).toHaveClass('magent-stop')
    expect(status.querySelector('.magent-clock')?.nextElementSibling).toBe(stop)

    await userEvent.click(stop)
    expect(mockCancel).toHaveBeenCalledWith(8)
    expect(within(status).getByRole('button', { name: 'Stopping…' })).toBeDisabled()
    expect(status).toHaveTextContent('Stopping — finishing the current step')

    // The stopped run's POST returns 200 with the partial turn; the thread
    // shows what ran (with its undo) and the cancelled note.
    const user = message({ id: 3, content: 'Make three tasks' })
    const assistant = message({
      id: 4,
      role: 'assistant',
      content: null,
      stop_reason: 'cancelled',
      tool_calls: [
        { tool: 'create_task', arguments: { title: 'First' }, result: JSON.stringify({ id: 11, title: 'First' }), error: null },
        { tool: 'create_task', arguments: { title: 'Second' }, result: null, error: 'not run: the user stopped the agent run before this call' },
      ],
    })
    mockGet.mockResolvedValue({ ...detail, messages: [...detail.messages, user, assistant], message_count: 4 })
    await act(async () => { land({ user_message: user, assistant_message: assistant }) })

    expect(await screen.findByText(/You stopped the run/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Undo \(move to trash\): Created task “First”/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Stop/ })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Message the agent' })).toBeEnabled()
  })

  it('re-arms Stop when the server says nothing was running', async () => {
    mockPost.mockImplementation(() => new Promise(() => {}))
    mockCancel.mockResolvedValue({ cancel_requested: false })
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')

    const composer = screen.getByRole('textbox', { name: 'Message the agent' })
    fireEvent.change(composer, { target: { value: 'Plan my Thursday' } })
    fireEvent.submit(composer.closest('form')!)
    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }))

    expect(mockCancel).toHaveBeenCalledWith(8)
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeEnabled()
  })

  it('shows a run the server reports in flight after a reload, with Stop, and polls in the reply (#358)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = message({ id: 3, content: 'Split the relaunch epic' })
    mockGet.mockResolvedValue({
      ...detail,
      messages: [...detail.messages, user],
      message_count: 3,
      run_in_flight: true,
      run_started_at: new Date().toISOString(),
    })
    mockCancel.mockResolvedValue({ cancel_requested: true })
    renderAt('/agent/8')

    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Working — reading your projects')
    // One user turn on screen — the thread's, no optimistic repeat.
    expect(screen.getAllByText('Split the relaunch epic')).toHaveLength(1)
    expect(screen.getByRole('textbox', { name: 'Message the agent' })).toBeDisabled()

    fireEvent.click(within(status).getByRole('button', { name: 'Stop' }))
    expect(mockCancel).toHaveBeenCalledWith(8)
    expect(await within(status).findByRole('button', { name: 'Stopping…' })).toBeDisabled()

    const assistant = message({ id: 4, role: 'assistant', content: null, stop_reason: 'cancelled' })
    mockGet.mockResolvedValue({ ...detail, messages: [...detail.messages, user, assistant], message_count: 4 })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })
    expect(await screen.findByText(/You stopped the run/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Stop/ })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Message the agent' })).toBeEnabled()
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

  describe('dismissing row actions returns to the conversations sheet (#346)', () => {
    /** Long-press the conversations-sheet row titled `title` into its actions sheet. */
    async function longPressRow(title: string) {
      const content = rowContent(title)
      fireEvent.pointerDown(content, { button: 0, clientX: 200, pointerId: 1 })
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      fireEvent.pointerUp(content, { clientX: 200, pointerId: 1 })
      return screen.findByRole('dialog', { name: title })
    }

    /** Escape on a modal <dialog> arrives as a cancelable `cancel` event. */
    function pressEscape(dialog: HTMLElement) {
      fireEvent(dialog, new Event('cancel', { cancelable: true }))
    }

    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
    })

    it.each([
      ['Escape', (sheet: HTMLElement) => pressEscape(sheet)],
      ['Cancel', (sheet: HTMLElement) => fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }))],
      ['the grab handle', (sheet: HTMLElement) => fireEvent.click(within(sheet).getByRole('button', { name: 'Close conversation actions' }))],
    ])("%s from a row's actions reopens the list, focused on that row", async (_how, dismiss) => {
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')
      await openConversationsSheet()
      dismiss(await longPressRow('Weekly review'))

      const list = screen.getByRole('dialog', { name: 'Conversations' })
      expect(screen.queryByRole('dialog', { name: 'Weekly review' })).toBeNull()
      expect(within(list).getByRole('button', { name: /Weekly review/ })).toHaveFocus()
    })

    it("Escape and Cancel from the title row's ⋯ close everything", async () => {
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')
      await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
      pressEscape(screen.getByRole('dialog', { name: "What's overdue in Homelab?" }))
      expect(screen.queryByRole('dialog')).toBeNull()

      await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('dialog')).toBeNull()
    })

    it('dismissing Rename goes back to where the actions were opened from', async () => {
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')

      // From a row: back to the list.
      await openConversationsSheet()
      const actions = await longPressRow('Weekly review')
      await userEvent.click(within(actions).getByRole('button', { name: 'Rename' }))
      pressEscape(screen.getByRole('dialog', { name: 'Rename conversation' }))
      expect(screen.getByRole('dialog', { name: 'Conversations' })).toBeInTheDocument()
      expect(screen.queryByRole('dialog', { name: 'Rename conversation' })).toBeNull()

      // From the title row: closed.
      await userEvent.click(within(screen.getByRole('dialog', { name: 'Conversations' })).getByRole('button', { name: 'Done' }))
      await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
      await userEvent.click(screen.getByRole('button', { name: 'Rename' }))
      await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(mockRename).not.toHaveBeenCalled()
    })

    it("Delete from a row's actions still closes every sheet, so the undo bar is reachable", async () => {
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')
      await openConversationsSheet()
      const actions = await longPressRow('Weekly review')
      await userEvent.click(within(actions).getByRole('button', { name: 'Delete conversation' }))
      await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(6))
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(screen.getByText('Deleted · Weekly review')).toBeInTheDocument()
    })
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

  // #325 — both paging controls share one shape, keep their 44px row, and
  // stay focused (aria-disabled, not disabled) while their page loads.
  it('pages the thread back from a divider that keeps focus while it loads', async () => {
    const older = message({ id: 0, content: 'An older question' })
    let resolveOlder: (page: ConversationDetail) => void = () => {}
    mockGet.mockImplementation((_id, params) =>
      params?.before_id === undefined
        ? Promise.resolve({ ...detail, has_more: true, message_count: 3 })
        : new Promise<ConversationDetail>((resolve) => { resolveOlder = resolve }),
    )
    renderAt('/agent/8')
    const button = await screen.findByRole('button', { name: 'Load older messages' })
    expect(button).toHaveClass('magent-older')

    // Geometry jsdom lacks: the reader is at the top of a 1000px thread.
    const list = screen.getByRole('list', { name: 'Agent chat' })
    let scrollHeight = 1000
    let scrollTop = 0
    Object.defineProperty(list, 'scrollHeight', { configurable: true, get: () => scrollHeight })
    Object.defineProperty(list, 'scrollTop', {
      configurable: true,
      get: () => scrollTop,
      set: (value: number) => { scrollTop = value },
    })

    button.focus()
    await userEvent.keyboard('{Enter}')
    const loading = screen.getByRole('button', { name: 'Loading…' })
    expect(loading).toBe(button)
    expect(loading).toHaveAttribute('aria-disabled', 'true')
    expect(loading).toHaveAttribute('aria-busy', 'true')
    expect(loading).toHaveFocus()
    // A second press while loading is ignored, not a duplicate fetch.
    await userEvent.keyboard('{Enter}')
    expect(mockGet).toHaveBeenCalledTimes(2)
    expect(mockGet).toHaveBeenLastCalledWith(8, { before_id: 1 })

    scrollHeight = 1600
    await act(async () => resolveOlder({ ...detail, messages: [older], has_more: false, message_count: 3 }))
    expect(await screen.findByText('An older question')).toBeInTheDocument()
    // The page landed above the reader, who stays put: 600px more above.
    expect(scrollTop).toBe(600)
    expect(screen.queryByRole('button', { name: /older messages|Loading/ })).toBeNull()
  })

  it('pages the conversations sheet from the same divider', async () => {
    const page = Array.from({ length: 51 }, (_, i): Conversation => ({
      id: 100 + i,
      title: `Conversation ${i}`,
      created_at: '2026-09-01T09:00:00Z',
      updated_at: '2026-09-01T10:00:00Z',
    }))
    mockList.mockImplementation(async (params) => (params?.offset ? [] : [conversations[0], ...page.slice(0, 50)]))
    renderAt('/agent/8')
    await screen.findByText('Two tasks are overdue:')
    const sheet = await openConversationsSheet()
    const more = within(sheet).getByRole('button', { name: 'Load older conversations' })
    expect(more).toHaveClass('magent-older')
    await userEvent.click(more)
    await waitFor(() => expect(mockList).toHaveBeenCalledWith({ limit: 51, offset: 50 }))
    await waitFor(() =>
      expect(within(sheet).queryByRole('button', { name: /older conversations|Loading/ })).toBeNull(),
    )
  })

  describe('holding the mic toggles spoken replies (#326)', () => {
    beforeEach(() => {
      vi.stubGlobal('MediaRecorder', class {})
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: vi.fn() } })
    })
    afterEach(() => {
      Reflect.deleteProperty(navigator, 'mediaDevices')
    })

    async function hold(element: Element) {
      fireEvent.pointerDown(element, { button: 0, clientX: 300, clientY: 700, pointerId: 1 })
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      fireEvent.pointerUp(element, { clientX: 300, clientY: 700, pointerId: 1 })
      // The release's click (detail 1 — a pointer click, not a keyboard one).
      fireEvent.click(element, { detail: 1 })
    }

    it('a long-press flips spoken replies, announces it, and never taps the mic', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')
      const mic = screen.getByRole('button', { name: 'Start voice conversation' })

      await hold(mic)
      expect(micTap).not.toHaveBeenCalled()
      expect(localStorage.getItem('agent-voice-output')).toBe('off')
      expect(screen.getAllByRole('status').map((el) => el.textContent)).toContain('Spoken replies off')
      expect(mic.closest('.magent-mic')).toHaveClass('magent-mic--muted')

      // The ⋯ sheet's switch reflects the same preference.
      await userEvent.click(screen.getByRole('button', { name: 'Conversation actions' }))
      const sheet = await screen.findByRole('dialog', { name: "What's overdue in Homelab?" })
      expect(within(sheet).getByRole('switch', { name: /Spoken replies/ })).toHaveAttribute('aria-checked', 'false')
      await userEvent.keyboard('{Escape}')

      await hold(screen.getByRole('button', { name: 'Start voice conversation' }))
      expect(micTap).not.toHaveBeenCalled()
      expect(localStorage.getItem('agent-voice-output')).toBe('on')
      expect(screen.getAllByRole('status').map((el) => el.textContent)).toContain('Spoken replies on')

      // The echo clears itself.
      await act(async () => { await vi.advanceTimersByTimeAsync(2100) })
      expect(screen.queryByText('Spoken replies on')).toBeNull()
    })

    it('a short tap still reaches the mic and leaves spoken replies alone', async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      renderAt('/agent/8')
      await screen.findByText('Two tasks are overdue:')
      const mic = screen.getByRole('button', { name: 'Start voice conversation' })

      fireEvent.pointerDown(mic, { button: 0, clientX: 300, clientY: 700, pointerId: 1 })
      await act(async () => { await vi.advanceTimersByTimeAsync(150) })
      fireEvent.pointerUp(mic, { clientX: 300, clientY: 700, pointerId: 1 })
      fireEvent.click(mic, { detail: 1 })
      expect(micTap).toHaveBeenCalledTimes(1)

      // A press that wanders past the slop is not a long-press either.
      fireEvent.pointerDown(mic, { button: 0, clientX: 300, clientY: 700, pointerId: 1 })
      fireEvent.pointerMove(mic, { clientX: 300, clientY: 640, pointerId: 1 })
      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(localStorage.getItem('agent-voice-output')).toBeNull()
      expect(screen.queryByText('Spoken replies off')).toBeNull()
    })
  })
})
