import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createConversation, deleteConversation, postMessage } from '../../api/agent'
import { ApiError } from '../../api/client'
import type { Conversation, MessageExchange } from '../../types/agent'
import { useInlineAgentAsk } from './useInlineAgentAsk'

vi.mock('../../api/agent', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  postMessage: vi.fn(),
}))

const mockCreateConversation = vi.mocked(createConversation)
const mockPostMessage = vi.mocked(postMessage)
const mockDeleteConversation = vi.mocked(deleteConversation)

function conversation(id: number): Conversation {
  return {
    id,
    title: null,
    created_at: '2026-07-11T10:00:00Z',
    updated_at: '2026-07-11T10:00:00Z',
  }
}

function exchange(conversationId: number): MessageExchange {
  return {
    user_message: {
      id: 1,
      conversation_id: conversationId,
      role: 'user',
      content: 'plan my day',
      tool_calls: null,
      stop_reason: null,
      created_at: '2026-07-11T10:00:00Z',
    },
    assistant_message: {
      id: 2,
      conversation_id: conversationId,
      role: 'assistant',
      content: 'Here is your day.',
      tool_calls: null,
      stop_reason: null,
      created_at: '2026-07-11T10:00:01Z',
    },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useInlineAgentAsk', () => {
  it('keeps the created conversation id when the first message is rate limited', async () => {
    mockCreateConversation.mockResolvedValue(conversation(5))
    mockPostMessage.mockRejectedValue(new ApiError(429, { detail: 'slow down' }))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      expect(await result.current.ask('plan my day')).toBeNull()
    })

    expect(result.current.state).toEqual({
      phase: 'error',
      text: 'plan my day',
      message: 'Rate limited — give the agent a moment before sending more.',
      conversationId: 5,
    })
  })

  it('keeps the created conversation id when the message request is rejected', async () => {
    mockCreateConversation.mockResolvedValue(conversation(9))
    mockPostMessage.mockRejectedValue(new Error('Failed to fetch'))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      await result.current.ask('plan my day')
    })

    expect(result.current.state).toMatchObject({ phase: 'error', conversationId: 9 })
  })

  it('retries into the claimed conversation instead of creating another', async () => {
    mockCreateConversation.mockResolvedValue(conversation(5))
    mockPostMessage.mockRejectedValueOnce(new ApiError(429, null))
    mockPostMessage.mockRejectedValueOnce(new Error('Failed to fetch'))
    mockPostMessage.mockResolvedValueOnce(exchange(5))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      await result.current.ask('plan my day')
    })
    act(() => result.current.dismiss())
    await act(async () => {
      await result.current.ask('plan my day')
    })
    await act(async () => {
      expect(await result.current.ask('plan my day')).toBe('Here is your day.')
    })

    expect(mockCreateConversation).toHaveBeenCalledTimes(1)
    expect(mockPostMessage).toHaveBeenCalledTimes(3)
    expect(mockPostMessage).toHaveBeenLastCalledWith(5, 'plan my day')
    expect(result.current.state).toMatchObject({ phase: 'done', conversationId: 5 })
  })

  it('never deletes the conversation it created', async () => {
    mockCreateConversation.mockResolvedValue(conversation(5))
    mockPostMessage.mockRejectedValue(new ApiError(429, null))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      await result.current.ask('plan my day')
    })

    // A failed send must never trigger cleanup, ambiguous or not.
    expect(mockDeleteConversation).not.toHaveBeenCalled()
  })

  it('starts a fresh conversation after the claimed one is deleted (404)', async () => {
    mockCreateConversation.mockResolvedValueOnce(conversation(5))
    mockCreateConversation.mockResolvedValueOnce(conversation(6))
    mockPostMessage.mockRejectedValueOnce(new ApiError(429, null))
    mockPostMessage.mockRejectedValueOnce(new ApiError(404, null))
    mockPostMessage.mockResolvedValueOnce(exchange(6))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      await result.current.ask('plan my day')
    })
    await act(async () => {
      await result.current.ask('plan my day')
    })

    expect(mockCreateConversation).toHaveBeenCalledTimes(2)
    expect(result.current.state).toMatchObject({ phase: 'done', conversationId: 6 })
  })

  it('reports a null conversation id when creation itself fails', async () => {
    mockCreateConversation.mockRejectedValue(new Error('Failed to fetch'))

    const { result } = renderHook(() => useInlineAgentAsk())
    await act(async () => {
      await result.current.ask('plan my day')
    })

    expect(result.current.state).toMatchObject({ phase: 'error', conversationId: null })
    expect(mockPostMessage).not.toHaveBeenCalled()
  })
})
