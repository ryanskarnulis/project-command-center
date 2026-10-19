import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cancelRun,
  getConversation,
  listConversations,
  postMessage,
} from '../../api/agent'
import { RequireRouteId } from '../../routes/RequireRouteId'
import type { AgentMessage, ConversationDetail, MessageExchange } from '../../types/agent'
import { AgentPage } from './AgentPage'
import { PendingExchange } from './PendingExchange'

/* Desktop Stop (#324): the control itself, then its wiring through AgentPage.
 * (Kept out of AgentPage.test.tsx deliberately — see the PR.) */

vi.mock('../../api/agent', () => ({
  cancelRun: vi.fn(),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  renameConversation: vi.fn(),
  restoreConversation: vi.fn(),
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
vi.mock('../../api/projects', () => ({ deleteProject: vi.fn(), restoreProject: vi.fn() }))

const mockCancel = vi.mocked(cancelRun)
const mockGet = vi.mocked(getConversation)
const mockList = vi.mocked(listConversations)
const mockPost = vi.mocked(postMessage)

function renderPending(props: Parameters<typeof PendingExchange>[0]) {
  return render(
    <ul>
      <PendingExchange {...props} />
    </ul>,
  )
}

describe('PendingExchange', () => {
  it('offers Stop when given a handler, and calls it', async () => {
    const onStop = vi.fn()
    renderPending({ text: 'Make three tasks', onStop })

    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent(/Working/)
  })

  it('disables Stop and says so while the stop is pending', () => {
    renderPending({ text: 'Make three tasks', onStop: vi.fn(), stopping: true })

    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(/Stopping/)
  })

  it('has no Stop where the caller cannot stop the run', () => {
    renderPending({ text: 'Make three tasks' })
    expect(screen.queryByRole('button')).toBeNull()
  })
})

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

const detail: ConversationDetail = {
  id: 1,
  title: 'Tasks',
  created_at: '2026-07-11T10:00:00Z',
  updated_at: '2026-07-11T10:00:00Z',
  messages: [],
  message_count: 0,
  has_more: false,
}

describe('AgentPage desktop — Stop', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    localStorage.clear()
    mockList.mockResolvedValue([detail])
    mockGet.mockResolvedValue(detail)
  })

  it('stops the in-flight run and shows the truthful cancelled turn', async () => {
    let land: (exchange: MessageExchange) => void = () => {}
    mockPost.mockImplementation(() => new Promise((resolve) => { land = resolve }))
    mockCancel.mockResolvedValue({ cancel_requested: true })

    render(
      <MemoryRouter initialEntries={['/agent/1']}>
        <Routes>
          <Route
            path="/agent/:conversationId"
            element={
              <RequireRouteId param="conversationId">
                <AgentPage />
              </RequireRouteId>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByText(/What should the agent do/)

    const composer = screen.getByRole('textbox', { name: 'Message the agent' })
    fireEvent.change(composer, { target: { value: 'Make two tasks' } })
    fireEvent.submit(composer.closest('form')!)

    await userEvent.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(mockCancel).toHaveBeenCalledWith(1)
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled()

    const user = message({ id: 3, content: 'Make two tasks' })
    const assistant = message({
      id: 4,
      role: 'assistant',
      content: null,
      stop_reason: 'cancelled',
      tool_calls: [
        {
          tool: 'create_task',
          arguments: { data: { title: 'Second' } },
          result: null,
          error: 'not run: the user stopped the agent run before this call',
        },
      ],
    })
    mockGet.mockResolvedValue({ ...detail, messages: [user, assistant], message_count: 2 })
    await act(async () => {
      land({ user_message: user, assistant_message: assistant })
    })

    const note = await screen.findByText(/You stopped the run/)
    expect(note).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Stop/ })).toBeNull()
    // The skipped call is listed with its reason — nothing claims it ran.
    const turn = note.closest<HTMLElement>('.agent-message')!
    expect(within(turn).getByText(/not run: the user stopped/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
