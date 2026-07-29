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
 *
 * Each page is read with a one-row lookahead (#216): a *full* page proves only
 * that the page is full, so asking for one row past it is what proves another
 * row exists. The lookahead row is never part of this window — it is the first
 * row of the next page, read again when that page is fetched.
 */
async function fetchWindow(
  size: number,
): Promise<{ conversations: Conversation[]; hasMore: boolean }> {
  const byId = new Map<number, Conversation>()
  let hasMore = false
  for (let offset = 0; offset < size; offset += CONVERSATION_PAGE_SIZE) {
    const page = await listConversations({
      limit: CONVERSATION_PAGE_SIZE + 1,
      offset,
    })
    for (const conversation of page.slice(0, CONVERSATION_PAGE_SIZE))
      byId.set(conversation.id, conversation)
    // Without the lookahead row, this page is the end of the list.
    hasMore = page.length > CONVERSATION_PAGE_SIZE
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
  // The largest window anyone has *asked* for, including requests still in
  // flight. "Load older" records its intent here before awaiting, so a refresh
  // that started (and finishes) while that page is loading is recognised as too
  // small and cannot cancel the click (#221).
  const requestedWindow = useRef(CONVERSATION_PAGE_SIZE)
  // Ordering guards (#211). Requests are multi-page and can overlap — a refresh
  // started while "Load older" is still in flight captures the *old* window
  // size, so without these an out-of-order completion would hide rows the user
  // just loaded.
  const requestSeq = useRef(0)
  const committedSeq = useRef(0)

  const load = useCallback(async function load(size: number): Promise<void> {
    const seq = ++requestSeq.current
    // Publish the intent before the first await: whichever request finishes
    // first, the largest window asked for is the one that ends up on screen.
    requestedWindow.current = Math.max(requestedWindow.current, size)
    try {
      const result = await fetchWindow(size)
      // Something newer already landed — this result is stale, drop it.
      if (seq <= committedSeq.current) return
      if (size < requestedWindow.current) {
        // Fresher data, but a smaller window than what is on screen or than a
        // "Load older" click is still fetching. Re-read at the requested size
        // rather than shrinking the list back (#211) or dropping this result
        // and losing the fresher data with it (#221).
        await load(requestedWindow.current)
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

  // A failed create propagates to the caller rather than writing `error`: that
  // state belongs to the list load and is cleared by the next successful
  // refresh, so a mutation failure parked there can vanish before it is read
  // (#219). Not safe to fire-and-forget — the caller must catch and surface it.
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
