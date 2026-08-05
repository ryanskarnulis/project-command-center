import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
  postMessage,
} from '../../api/agent'
import { deleteTask } from '../../api/tasks'
import type { AgentMessage, ConversationDetail } from '../../types/agent'
import { AgentPage } from './AgentPage'

vi.mock('../../api/agent', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  postMessage: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  deleteTask: vi.fn(),
  markTaskDone: vi.fn(),
  reopenTask: vi.fn(),
  restoreTask: vi.fn(),
}))

vi.mock('../../api/projects', () => ({
  deleteProject: vi.fn(),
  restoreProject: vi.fn(),
}))

const mockList = vi.mocked(listConversations)
const mockCreate = vi.mocked(createConversation)
const mockDelete = vi.mocked(deleteConversation)
const mockGet = vi.mocked(getConversation)
const mockPost = vi.mocked(postMessage)

function message(overrides: Partial<AgentMessage>): AgentMessage {
  return {
    id: 1,
    conversation_id: 1,
    role: 'user',
    content: 'hello',
    tool_calls: null,
    stop_reason: null,
    created_at: '2026-07-11T10:00:00Z',
    ...overrides,
  }
}

const assistantWithToolCalls = message({
  id: 2,
  role: 'assistant',
  content: 'Created the task.',
  stop_reason: 'completed',
  tool_calls: [
    {
      tool: 'create_task',
      arguments: { data: { title: 'Water plants' } },
      result: JSON.stringify({ id: 7, title: 'Water plants' }),
      error: null,
    },
    {
      tool: 'search',
      arguments: { query: 'plants' },
      result: '{"projects": [], "tasks": []}',
      error: null,
    },
  ],
})

const detail: ConversationDetail = {
  id: 1,
  title: 'Water the plants',
  created_at: '2026-07-11T10:00:00Z',
  updated_at: '2026-07-11T10:00:00Z',
  messages: [message({ content: 'Create a task to water plants' }), assistantWithToolCalls],
  message_count: 2,
  has_more: false,
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agent" element={<AgentPage />} />
        <Route path="/agent/:conversationId" element={<AgentPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** 51 conversations, newest first — one more than a server page. */
const manyConversations = Array.from({ length: 51 }, (_, index) => ({
  id: 51 - index,
  title: `Conversation ${51 - index}`,
  created_at: detail.created_at,
  updated_at: detail.updated_at,
}))

/** Serve every list request from a deferred promise so the test controls the
 * completion order. `settle(n)` resolves the n-th request made so far. */
function deferConversationPages() {
  const pending: (() => void)[] = []
  mockList.mockImplementation(async (params) => {
    const offset = params?.offset ?? 0
    const limit = params?.limit ?? 50
    return new Promise((resolve) => {
      pending.push(() =>
        resolve(manyConversations.slice(offset, offset + limit)),
      )
    })
  })
  mockCreate.mockResolvedValue({
    id: 99,
    title: null,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
  })

  async function settle(index: number): Promise<void> {
    await waitFor(() => expect(pending.length).toBeGreaterThan(index))
    await act(async () => {
      pending[index]()
    })
  }

  /** Resolve every outstanding request, and anything they start, in order. */
  async function settleAll(): Promise<void> {
    for (let index = 0; index < pending.length; index += 1) await settle(index)
  }
  return { pending, settle, settleAll }
}

/** Titles currently rendered in the conversation sidebar. */
function visibleConversations(): (string | null)[] {
  return screen
    .getAllByRole('button', { name: /^Conversation \d+/ })
    .map((button) => button.textContent)
}

beforeEach(() => {
  vi.clearAllMocks()
  mockList.mockResolvedValue([
    { id: 1, title: 'Water the plants', created_at: detail.created_at, updated_at: detail.updated_at },
  ])
  mockGet.mockResolvedValue(detail)
})

describe('AgentPage', () => {
  it('renders the thread with tool calls and an undo affordance', async () => {
    renderAt('/agent/1')

    expect(await screen.findByText('Created the task.')).toBeInTheDocument()
    expect(screen.getByText('Created task “Water plants”')).toBeInTheDocument()
    expect(screen.getByText('Searched for “plants”')).toBeInTheDocument()

    // Only the mutation gets an undo; clicking it trashes the created task.
    const undo = screen.getByRole('button', { name: 'Undo (move to trash)' })
    fireEvent.click(undo)
    await waitFor(() => expect(vi.mocked(deleteTask)).toHaveBeenCalledWith(7))
    expect(await screen.findByText('Undone')).toBeInTheDocument()
  })

  it('surfaces rejected conversation create and delete actions', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockCreate.mockRejectedValue(new Error('Create failed'))
    mockDelete.mockRejectedValue(new Error('Delete failed'))
    renderAt('/agent')

    const createButton = await screen.findByRole('button', {
      name: 'New conversation',
    })
    fireEvent.click(createButton)
    await waitFor(() => expect(mockCreate).toHaveBeenCalledOnce())
    expect(await screen.findByText('Create failed')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Delete conversation Water the plants',
      }),
    )
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(1))
    expect(await screen.findByText('Delete failed')).toBeInTheDocument()
    expect(screen.getByText('Water the plants')).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  // A delete failure used to be written into the hook's `error` — the slot the
  // list load owns and clears on every successful refresh. Since an agent turn
  // refreshes the sidebar, the reason could vanish before it was read (#229).
  it('keeps a rejected delete visible across a later refresh, with the API reason (#229)', async () => {
    const { ApiError } = await import('../../api/client')
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockDelete.mockRejectedValue(
      new ApiError(409, { detail: 'That conversation has a run in flight.' }),
    )
    renderAt('/agent/1')
    await screen.findByText('Created the task.')

    fireEvent.click(
      screen.getByRole('button', { name: 'Delete conversation Water the plants' }),
    )
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(1))

    // The API's `detail` explains the refusal; "API error 409" would not.
    expect(
      await screen.findByText('That conversation has a run in flight.'),
    ).toBeInTheDocument()
    // Nothing navigated away, and the conversation is still listed.
    expect(screen.getByText('Water the plants')).toBeInTheDocument()
    expect(screen.getByText('Created the task.')).toBeInTheDocument()

    // An agent turn refreshes the sidebar list, which clears the load's own
    // error. The delete failure must survive it.
    mockPost.mockResolvedValue({
      user_message: message({ id: 3, content: 'Now complete it' }),
      assistant_message: message({
        id: 4,
        role: 'assistant',
        content: 'Done.',
        stop_reason: 'completed',
      }),
    })
    fireEvent.change(screen.getByLabelText('Message the agent'), {
      target: { value: 'Now complete it' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    await waitFor(() => expect(mockPost).toHaveBeenCalledOnce())
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))

    expect(
      screen.getByText('That conversation has a run in flight.'),
    ).toBeInTheDocument()
    confirmSpy.mockRestore()
  })

  // Both "New conversation" buttons fire-and-forget `startConversation`, so a
  // rejected create used to look exactly like an ignored click (#219).
  it('keeps a failed new conversation on screen and retryable (#219)', async () => {
    const { ApiError } = await import('../../api/client')
    mockCreate.mockRejectedValue(new ApiError(503, { detail: 'Backend unavailable' }))
    renderAt('/agent')

    fireEvent.click(
      await screen.findByRole('button', { name: 'New conversation' }),
    )

    // The reason is shown, and nothing navigated to a conversation that was
    // never created.
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Backend unavailable',
    )
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()
    expect(mockGet).not.toHaveBeenCalled()

    // The empty-state button is the same action and reports the same way.
    fireEvent.click(
      screen.getByRole('button', { name: 'Start a conversation' }),
    )
    await waitFor(() => expect(mockCreate).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('alert')).toHaveTextContent('Backend unavailable')
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()

    // A retry that succeeds clears the failure and opens the conversation.
    mockCreate.mockResolvedValueOnce({
      id: 99,
      title: null,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    })
    fireEvent.click(screen.getByRole('button', { name: 'New conversation' }))

    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(99))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('sends a message, shows progress, then renders the refetched thread', async () => {
    let resolveRun: (value: never) => void = () => {}
    mockPost.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRun = resolve as (value: never) => void
        }),
    )
    renderAt('/agent/1')
    await screen.findByText('Created the task.')

    fireEvent.change(screen.getByLabelText('Message the agent'), {
      target: { value: 'Now complete it' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    // Optimistic user bubble + working indicator while the loop runs.
    expect(await screen.findByText('Now complete it')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/Working/)

    const followup: ConversationDetail = {
      ...detail,
      messages: [
        ...detail.messages,
        message({ id: 3, content: 'Now complete it' }),
        message({ id: 4, role: 'assistant', content: 'Done — marked it complete.', stop_reason: 'completed' }),
      ],
    }
    mockGet.mockResolvedValue(followup)
    resolveRun(undefined as never)

    expect(await screen.findByText('Done — marked it complete.')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('explains a run that stopped without a reply', async () => {
    mockGet.mockResolvedValue({
      ...detail,
      messages: [
        message({ content: 'Do something impossible' }),
        message({ id: 5, role: 'assistant', content: null, stop_reason: 'max_iterations' }),
      ],
    })
    renderAt('/agent/1')

    expect(
      await screen.findByText(/hit its step limit before finishing/),
    ).toBeInTheDocument()
  })

  it('reaches conversations past the first page via load more (#193)', async () => {
    // 51 active conversations, newest first — one more than a server page.
    const all = Array.from({ length: 51 }, (_, index) => ({
      id: 51 - index,
      title: `Conversation ${51 - index}`,
      created_at: detail.created_at,
      updated_at: detail.updated_at,
    }))
    mockList.mockImplementation(async (params) =>
      all.slice(params?.offset ?? 0, (params?.offset ?? 0) + (params?.limit ?? 50)),
    )

    renderAt('/agent')
    expect(await screen.findByText('Conversation 51')).toBeInTheDocument()
    // The oldest one falls outside the first page.
    expect(screen.queryByText('Conversation 1')).not.toBeInTheDocument()

    fireEvent.click(
      screen.getByRole('button', { name: 'Load older conversations' }),
    )

    expect(await screen.findByText('Conversation 1')).toBeInTheDocument()
    // Every conversation is listed exactly once — no duplicates, no gaps.
    const titles = screen
      .getAllByRole('button', { name: /^Conversation \d+/ })
      .map((button) => button.textContent)
    expect(titles).toHaveLength(51)
    expect(new Set(titles).size).toBe(51)
    // The list is exhausted, so the affordance goes away.
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: /Load older conversations/ }),
      ).not.toBeInTheDocument(),
    )
  })

  // A full page proves the page is full, not that another row exists, so the
  // window is read with a one-row lookahead (#216). 50 is the exact boundary:
  // it used to offer "Load older" and then answer it with an empty page.
  it.each([
    { total: 49, expectsMore: false },
    { total: 50, expectsMore: false },
    { total: 51, expectsMore: true },
  ])(
    'offers "Load older" for $total conversations only when older ones exist (#216)',
    async ({ total, expectsMore }) => {
      const all = Array.from({ length: total }, (_, index) => ({
        id: total - index,
        title: `Conversation ${total - index}`,
        created_at: detail.created_at,
        updated_at: detail.updated_at,
      }))
      mockList.mockImplementation(async (params) => {
        const offset = params?.offset ?? 0
        return all.slice(offset, offset + (params?.limit ?? 50))
      })

      renderAt('/agent')
      await screen.findByText(`Conversation ${total}`)
      await waitFor(() =>
        expect(
          screen.getAllByRole('button', { name: /^Conversation \d+/ }),
        ).toHaveLength(Math.min(total, 50)),
      )

      const loadOlder = screen.queryByRole('button', {
        name: /Load older conversations/,
      })
      if (expectsMore) {
        expect(loadOlder).toBeInTheDocument()
      } else {
        expect(loadOlder).not.toBeInTheDocument()
      }
      // One page plus its lookahead row — never a speculative second page.
      expect(mockList).toHaveBeenCalledTimes(1)
      expect(mockList).toHaveBeenCalledWith({ limit: 51, offset: 0 })
    },
  )

  it('keeps the loaded window when a stale refresh resolves last (#211)', async () => {
    const { pending, settle } = deferConversationPages()

    renderAt('/agent')

    // 1. Initial 50-row window.
    await settle(0)
    expect(await screen.findByText('Conversation 51')).toBeInTheDocument()
    expect(screen.queryByText('Conversation 1')).not.toBeInTheDocument()

    // 2. Start "Load older" (window 100) but leave its first page pending.
    fireEvent.click(
      screen.getByRole('button', { name: 'Load older conversations' }),
    )
    await waitFor(() => expect(pending).toHaveLength(2))

    // 3. Trigger a refresh while that is in flight — it captures window 50.
    fireEvent.click(screen.getByRole('button', { name: /New conversation/ }))
    await waitFor(() => expect(pending).toHaveLength(3))

    // 4. The load-more request completes first: both of its pages land.
    await settle(1)
    await settle(3)
    expect(await screen.findByText('Conversation 1')).toBeInTheDocument()

    // 5. The stale 50-row refresh resolves last and must not shrink the window.
    await settle(2)
    // Its guard re-reads at the committed size; drain whatever that starts.
    await settle(4)
    await settle(5)

    expect(visibleConversations()).toHaveLength(51)
    expect(screen.getByText('Conversation 1')).toBeInTheDocument()
    // Window stayed at >= 100: the affordance does not come back.
    expect(
      screen.queryByRole('button', { name: /Load older conversations/ }),
    ).not.toBeInTheDocument()
  })

  it('keeps a pending "Load older" when a refresh resolves first (#221)', async () => {
    // The inverse completion order of #211: the *newer* refresh lands first, so
    // request freshness alone would discard the larger window the user asked
    // for and the click would silently do nothing.
    const { pending, settle, settleAll } = deferConversationPages()

    renderAt('/agent')

    // 1. Initial 50-row window.
    await settle(0)
    expect(await screen.findByText('Conversation 51')).toBeInTheDocument()
    expect(screen.queryByText('Conversation 1')).not.toBeInTheDocument()

    // 2. Start "Load older" (window 100) but leave its first page pending.
    fireEvent.click(
      screen.getByRole('button', { name: 'Load older conversations' }),
    )
    await waitFor(() => expect(pending).toHaveLength(2))
    expect(mockList).toHaveBeenLastCalledWith({ limit: 51, offset: 0 })

    // 3. Trigger a refresh while that is in flight — it captures window 50.
    fireEvent.click(screen.getByRole('button', { name: /New conversation/ }))
    await waitFor(() => expect(pending).toHaveLength(3))

    // 4. The newer refresh completes first. It may not commit its smaller
    //    window over the pagination still in flight.
    await settle(2)
    expect(visibleConversations()).toHaveLength(50)

    // 5. The older, larger request lands last — and must still be honoured.
    await settleAll()

    expect(visibleConversations()).toHaveLength(51)
    expect(screen.getByText('Conversation 1')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /Load older conversations/ }),
    ).not.toBeInTheDocument()
  })

  it('surfaces a rate-limit rejection and reloads the thread', async () => {
    const { ApiError } = await import('../../api/client')
    mockPost.mockRejectedValue(new ApiError(429, { detail: 'rate limit exceeded' }))
    renderAt('/agent/1')
    await screen.findByText('Created the task.')

    fireEvent.change(screen.getByLabelText('Message the agent'), {
      target: { value: 'again' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Rate limited/)
  })
})
