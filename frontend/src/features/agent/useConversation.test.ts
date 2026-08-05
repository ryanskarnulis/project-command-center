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

/** A promise plus the handles to settle it later. */
function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
} {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  // Nothing rejects an unused deferred, and every rejection below is awaited
  // through the hook — this only keeps Vitest from flagging the gap between
  // creating the promise and the hook attaching its handler.
  promise.catch(() => {})
  return { promise, resolve, reject }
}

const exchange = (id: number): MessageExchange => ({
  user_message: message(3, id, 'sent'),
  assistant_message: message(4, id, 'reply'),
})

const contents = (d: ConversationDetail | null): (string | null)[] =>
  (d?.messages ?? []).map((m) => m.content)

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

  it('keeps the committed exchange visible when the post-send refetch fails (#233)', async () => {
    const aLoad = detail(1, ['old'])
    const refetchA = deferred<ConversationDetail>()

    mockGet.mockImplementation((id: number) => {
      if (id === 2) return Promise.resolve(detail(2, ['b first']))
      return mockGet.mock.calls.filter((c) => c[0] === 1).length === 1
        ? Promise.resolve(aLoad)
        : refetchA.promise
    })
    mockPost.mockResolvedValue(exchange(1))

    const { result, rerender } = renderHook(({ id }: { id: number }) => useConversation(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.detail).toEqual(aLoad))

    let sent!: Promise<string | null>
    act(() => {
      sent = result.current.send('sent')
    })
    await waitFor(() => expect(result.current.pendingText).toBe('sent'))

    // The run committed; only the source-of-truth reload fails.
    await act(async () => {
      refetchA.reject(new Error('refresh failed'))
      expect(await sent).toBe('reply')
    })

    // The turn stays on screen instead of reverting to the pre-send thread.
    expect(contents(result.current.detail)).toEqual(['old', 'sent', 'reply'])
    expect(result.current.pendingText).toBeNull()
    expect(result.current.loading).toBe(false)
    // Worded as a refresh failure, not a run failure: the reply did land.
    expect(result.current.error).toContain('The agent replied')
    expect(result.current.error).toContain('refresh failed')

    // And the error belongs to conversation 1 only.
    rerender({ id: 2 })
    await waitFor(() => expect(result.current.detail?.id).toBe(2))
    expect(result.current.error).toBeNull()
  })

  it('keeps the send error when the send and the refetch both fail (#233)', async () => {
    const aLoad = detail(1, ['old'])
    mockGet.mockImplementation(() =>
      mockGet.mock.calls.length === 1
        ? Promise.resolve(aLoad)
        : Promise.reject(new Error('refresh failed')),
    )
    mockPost.mockRejectedValue(new Error('the agent run exploded'))

    const { result } = renderHook(({ id }: { id: number }) => useConversation(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.detail).toEqual(aLoad))

    await act(async () => {
      expect(await result.current.send('sent')).toBeNull()
    })

    // The run failure is the one the user needs; nothing was committed to merge.
    expect(result.current.error).toBe('the agent run exploded')
    expect(contents(result.current.detail)).toEqual(['old'])
    expect(result.current.pendingText).toBeNull()
  })

  it('clears a load error once the same conversation loads successfully (#233)', async () => {
    const recovered = detail(1, ['recovered'])
    mockGet.mockImplementation((id: number) => {
      if (id === 2) return Promise.resolve(detail(2, ['b first']))
      return mockGet.mock.calls.filter((c) => c[0] === 1).length === 1
        ? Promise.reject(new Error('first failed'))
        : Promise.resolve(recovered)
    })

    const { result, rerender } = renderHook(({ id }: { id: number }) => useConversation(id), {
      initialProps: { id: 1 },
    })
    await waitFor(() => expect(result.current.error).toBe('first failed'))
    expect(result.current.detail).toBeNull()
    expect(result.current.loading).toBe(false)

    // The error is tagged to 1, so 2 never inherits it.
    rerender({ id: 2 })
    await waitFor(() => expect(result.current.detail?.id).toBe(2))
    expect(result.current.error).toBeNull()

    // Back on 1, the recovered thread renders without the obsolete alert.
    rerender({ id: 1 })
    await waitFor(() => expect(contents(result.current.detail)).toEqual(['recovered']))
    expect(result.current.error).toBeNull()
  })
})
