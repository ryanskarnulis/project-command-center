import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { listConversations } from '../../api/agent'
import type { Conversation } from '../../types/agent'
import { CONVERSATION_PAGE_SIZE, useConversations } from './useConversations'

vi.mock('../../api/agent', () => ({
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  listConversations: vi.fn(),
}))

const mockList = vi.mocked(listConversations)

function conversation(id: number): Conversation {
  return {
    id,
    title: `Conversation ${id}`,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

/** `count` conversations with consecutive ids starting at `from`. A page of
 * `CONVERSATION_PAGE_SIZE + 1` is what the hook reads as "there is more". */
const page = (from: number, count: number): Conversation[] =>
  Array.from({ length: count }, (_, i) => conversation(from + i))

const ids = (list: Conversation[]): number[] => list.map((c) => c.id)

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
  // Every rejection below is awaited through the hook; this only keeps Vitest
  // from flagging the gap before the hook attaches its handler.
  promise.catch(() => {})
  return { promise, resolve, reject }
}

describe('useConversations', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('loads the newest page on mount', async () => {
    const first = page(1, 3)
    mockList.mockResolvedValue(first)

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(mockList).toHaveBeenCalledWith({
      limit: CONVERSATION_PAGE_SIZE + 1,
      offset: 0,
    })
    expect(result.current.conversations).toEqual(first)
    expect(result.current.hasMore).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('drops a superseded load failure instead of erroring over fresh rows (#261)', async () => {
    const initial = page(1, 2)
    const fresh = page(10, 3)
    const slow = deferred<Conversation[]>()
    mockList
      .mockResolvedValueOnce(initial)
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValue(fresh)

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toEqual(initial))

    // A refresh starts and stalls...
    let stale!: Promise<void>
    act(() => {
      stale = result.current.refresh()
    })
    // ...a newer one overtakes it and commits fresh rows.
    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.conversations).toEqual(fresh)

    // Only now does the superseded request fail.
    await act(async () => {
      slow.reject(new Error('conversations request died'))
      await stale
    })

    // Its rows would have been discarded, so its failure is discarded too.
    expect(result.current.error).toBeNull()
    expect(result.current.conversations).toEqual(fresh)
    expect(result.current.loading).toBe(false)
  })

  it('keeps a fresh sidebar when a slow "Load older" fails last (#261)', async () => {
    // The issue's reproduction: "Load older" stalls, a refresh commits a bigger
    // window in the meantime, and the superseded page read then errors out.
    const older = deferred<Conversation[]>()
    const initialFirst = page(1, CONVERSATION_PAGE_SIZE + 1)
    const freshFirst = page(100, CONVERSATION_PAGE_SIZE + 1)
    const freshSecond = page(200, 3)
    let calls = 0
    mockList.mockImplementation((params = {}) => {
      calls += 1
      if (calls === 1) return Promise.resolve(initialFirst)
      // The "Load older" read that never lands.
      if (calls === 2) return older.promise
      return Promise.resolve(params.offset === 0 ? freshFirst : freshSecond)
    })

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.hasMore).toBe(true))

    let loadingOlder!: Promise<void>
    act(() => {
      loadingOlder = result.current.loadMore()
    })
    expect(result.current.loadingMore).toBe(true)

    // The refresh re-reads at the window "Load older" asked for and commits.
    await act(async () => {
      await result.current.refresh()
    })
    expect(ids(result.current.conversations)).toEqual([
      ...ids(freshFirst.slice(0, CONVERSATION_PAGE_SIZE)),
      ...ids(freshSecond),
    ])

    await act(async () => {
      older.reject(new Error('load older died'))
      await loadingOlder
    })

    expect(result.current.error).toBeNull()
    expect(result.current.conversations).toHaveLength(CONVERSATION_PAGE_SIZE + 3)
    expect(result.current.hasMore).toBe(false)
    expect(result.current.loadingMore).toBe(false)
  })

  it('surfaces a failure from the request that is still current (#261)', async () => {
    const initial = page(1, 2)
    mockList
      .mockResolvedValueOnce(initial)
      .mockRejectedValue(new Error('conversations are gone'))

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.conversations).toEqual(initial))

    // Nothing newer committed, so this failure is the current state of the list.
    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.error).toBe('conversations are gone')
    // The last known-good rows stay on screen under the banner.
    expect(result.current.conversations).toEqual(initial)
  })

  it('surfaces a failed initial load', async () => {
    mockList.mockRejectedValue(new Error('offline'))

    const { result } = renderHook(() => useConversations())
    await waitFor(() => expect(result.current.error).toBe('offline'))

    expect(result.current.loading).toBe(false)
    expect(result.current.conversations).toEqual([])
  })
})
