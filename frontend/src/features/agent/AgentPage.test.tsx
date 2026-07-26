import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  it('swallows rejected conversation create and delete actions', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    mockCreate.mockRejectedValue(new Error('Create failed'))
    mockDelete.mockRejectedValue(new Error('Delete failed'))
    renderAt('/agent')

    const createButton = await screen.findByRole('button', {
      name: 'New conversation',
    })
    fireEvent.click(createButton)
    await waitFor(() => expect(mockCreate).toHaveBeenCalledOnce())
    expect(screen.getByRole('heading', { name: 'Agent' })).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Delete conversation Water the plants',
      }),
    )
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(1))
    expect(screen.getByText('Water the plants')).toBeInTheDocument()
    confirmSpy.mockRestore()
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
