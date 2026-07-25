import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getConversation, postMessage } from '../../api/agent'
import type { AgentMessage, ConversationDetail, MessageExchange } from '../../types/agent'
import { useConversation } from './useConversation'

vi.mock('../../api/agent', () => ({
  getConversation: vi.fn(),
  postMessage: vi.fn(),
}))

const mockGet = vi.mocked(getConversation)
const mockPost = vi.mocked(postMessage)

function message(id: number, conversationId: number, content: string): AgentMessage {
  return {
    id,
    conversation_id: conversationId,
    role: id % 2 === 1 ? 'user' : 'assistant',
    content,
    tool_calls: null,
    stop_reason: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function detail(id: number, contents: string[]): ConversationDetail {
  return {
    id,
    title: `Conversation ${id}`,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    messages: contents.map((c, i) => message(i + 1, id, c)),
  }
}

/** A promise plus the handle to settle it later. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('useConversation', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('loads the conversation for the current id', async () => {
    mockGet.mockResolvedValue(detail(1, ['hi']))
    const { result } = renderHook(({ id }: { id: number }) => useConversation(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.detail?.id).toBe(1))
    expect(result.current.loading).toBe(false)
  })

  it("does not let a slow send's refetch clobber the conversation navigated to", async () => {
    const aLoad = detail(1, ['a first'])
    const aRefetch = detail(1, ['a first', 'a sent', 'a reply'])
    const bLoad = detail(2, ['b first'])

    const post = deferred<MessageExchange>()
    const refetchA = deferred<ConversationDetail>()
    const loadB = deferred<ConversationDetail>()

    mockGet.mockImplementation((id: number) => {
      if (id === 2) return loadB.promise
      // First call for A is the initial load; the second is the post-send
      // refetch we want to arrive last.
      return mockGet.mock.calls.filter((c) => c[0] === 1).length === 1
        ? Promise.resolve(aLoad)
        : refetchA.promise
    })
    mockPost.mockReturnValue(post.promise)

    const { result, rerender } = renderHook(({ id }: { id: number }) => useConversation(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.detail).toEqual(aLoad))

    // 1. Send on A; the run is still in flight.
    let sent: Promise<string | null>
    act(() => {
      sent = result.current.send('a sent')
    })
    await waitFor(() => expect(result.current.pendingText).toBe('a sent'))

    // 2. Navigate to B while A's send is pending, and let B load.
    rerender({ id: 2 })
    await act(async () => {
      loadB.resolve(bLoad)
      await loadB.promise
    })
    expect(result.current.detail).toEqual(bLoad)

    // 3. A's send and its refetch resolve last.
    await act(async () => {
      post.resolve({
        user_message: message(2, 1, 'a sent'),
        assistant_message: message(3, 1, 'a reply'),
      })
      refetchA.resolve(aRefetch)
      await sent
    })

    // B is still on screen and not stuck loading.
    expect(result.current.detail).toEqual(bLoad)
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })
})
