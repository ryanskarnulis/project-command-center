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

  const load = useCallback(async (size: number) => {
    try {
      const result = await fetchWindow(size)
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

  useEffect(() => {
    let active = true
    fetchWindow(CONVERSATION_PAGE_SIZE)
      .then((result) => {
        if (!active) return
        setConversations(result.conversations)
        setHasMore(result.hasMore)
      })
      .catch((e: unknown) => {
        if (active) {
          setError(e instanceof Error ? e.message : 'Failed to load conversations')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

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
