import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createConversation,
  deleteConversation,
  listConversations,
} from '../../api/agent'
import type { Conversation } from '../../types/agent'

/** Server page size for `GET /api/agent/conversations` (its default limit). */
export const CONVERSATION_PAGE_SIZE = 50

interface UseConversations {
  conversations: Conversation[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  error: string | null
  refresh: () => Promise<void>
  loadMore: () => Promise<void>
  create: () => Promise<Conversation>
  remove: (id: number) => Promise<void>
}

/** Read `size` conversations as bounded pages, newest first.
 *
 * The API is deliberately capped per request, so a larger window is assembled
 * from successive `limit`/`offset` pages. Always read from offset 0 (never
 * stitch a fresh page onto a stale window): a new turn bumps a conversation's
 * `updated_at` and reorders the list, which would otherwise duplicate or skip
 * rows. Ids are deduped as belt and braces against a write landing mid-read.
 */
async function fetchWindow(
  size: number,
): Promise<{ conversations: Conversation[]; hasMore: boolean }> {
  const byId = new Map<number, Conversation>()
  let hasMore = false
  for (let offset = 0; offset < size; offset += CONVERSATION_PAGE_SIZE) {
    const page = await listConversations({
      limit: CONVERSATION_PAGE_SIZE,
      offset,
    })
    for (const conversation of page) byId.set(conversation.id, conversation)
    // A short page means we reached the end of the list.
    hasMore = page.length === CONVERSATION_PAGE_SIZE
    if (!hasMore) break
  }
  return { conversations: [...byId.values()], hasMore }
}

/** The sidebar list: active conversations, most recently touched first.
 *
 * Every conversation stays reachable — `loadMore` grows the loaded window by
 * one page at a time (#193).
 */
export function useConversations(): UseConversations {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The window currently loaded; refreshes re-read exactly this much so a
  // background refresh never shrinks the list back to the first page.
  const windowSize = useRef(CONVERSATION_PAGE_SIZE)
  // Ordering guards (#211). Requests are multi-page and can overlap — a refresh
  // started while "Load older" is still in flight captures the *old* window
  // size, so without these an out-of-order completion would hide rows the user
  // just loaded.
  const requestSeq = useRef(0)
  const committedSeq = useRef(0)

  const load = useCallback(async function load(size: number): Promise<void> {
    const seq = ++requestSeq.current
    try {
      const result = await fetchWindow(size)
      // Something newer already landed — this result is stale, drop it.
      if (seq <= committedSeq.current) return
      if (size < windowSize.current) {
        // Fresher data, but a smaller window than what is on screen. Re-read at
        // the committed size rather than shrinking the list back.
        await load(windowSize.current)
        return
      }
      committedSeq.current = seq
      windowSize.current = size
      setConversations(result.conversations)
      setHasMore(result.hasMore)
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load conversations')
    }
  }, [])

  const refresh = useCallback(async () => {
    await load(windowSize.current)
    setLoading(false)
  }, [load])

  const loadMore = useCallback(async () => {
    setLoadingMore(true)
    try {
      await load(windowSize.current + CONVERSATION_PAGE_SIZE)
    } finally {
      setLoadingMore(false)
    }
  }, [load])

  // Initial read goes through `refresh` so it shares the ordering guards above.
  useEffect(() => {
    void refresh()
  }, [refresh])

  const create = useCallback(async () => {
    const conversation = await createConversation()
    await refresh()
    return conversation
  }, [refresh])

  const remove = useCallback(
    async (id: number) => {
      // The API rejects a delete while that conversation has a run in flight
      // (409, #149) — surface it instead of failing silently, and rethrow so the
      // caller doesn't treat the conversation as gone.
      try {
        await deleteConversation(id)
        setError(null)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to delete conversation')
        throw e
      }
      await refresh()
    },
    [refresh],
  )

  return {
    conversations,
    loading,
    loadingMore,
    hasMore,
    error,
    refresh,
    loadMore,
    create,
    remove,
  }
}
