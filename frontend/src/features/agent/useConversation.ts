import { useCallback, useEffect, useRef, useState } from 'react'
import { getConversation, postMessage } from '../../api/agent'
import type { ConversationDetail, MessageExchange } from '../../types/agent'
import { refreshErrorMessage, sendErrorMessage } from './errorMessage'

interface UseConversation {
  detail: ConversationDetail | null
  loading: boolean
  error: string | null
  /** Whether messages older than the loaded page exist (#244). */
  hasMore: boolean
  /** True while `loadOlder` is in flight. */
  loadingOlder: boolean
  /** Prepend the page immediately older than the oldest loaded message. */
  loadOlder: () => Promise<void>
  /** The optimistic user bubble + "working" indicator while a run is in flight. */
  pendingText: string | null
  /** Resolves to the assistant's reply text on success (possibly ''), or
   * null when the run failed — voice uses the text to speak the reply. */
  send: (content: string) => Promise<string | null>
}

// All state is tagged with the conversation it belongs to and the exposed
// values are derived by id match — switching conversations "resets" the view
// without effect-time setState, and stale data is never shown for the wrong
// thread. Tagging alone is not enough for writes, though: the state is a
// single slot, so a slow send's refetch landing after the user navigated away
// would overwrite the thread now on screen (which would then sit on "Loading"
// forever, since its own effect does not rerun). Every write is therefore also
// guarded against the id currently routed to (`currentIdRef`).
interface Tagged<T> {
  id: number
  value: T
}

function forId<T>(tagged: Tagged<T> | null, id: number | null): T | null {
  return tagged !== null && tagged.id === id ? tagged.value : null
}

/** The thread with the exchange the server just committed appended — the
 * fallback for when the post-send refetch fails (#233).
 *
 * Message ids are unique and server-assigned, so skipping the ones already
 * present keeps this idempotent if the pre-send thread somehow contains them.
 */
function withExchange(
  detail: ConversationDetail,
  exchange: MessageExchange,
): ConversationDetail {
  const seen = new Set(detail.messages.map((m) => m.id))
  const added = [exchange.user_message, exchange.assistant_message].filter(
    (m) => !seen.has(m.id),
  )
  return added.length === 0
    ? detail
    : {
        ...detail,
        messages: [...detail.messages, ...added],
        message_count: detail.message_count + added.length,
      }
}

/** A freshly fetched newest page, with any older pages the user had already
 * loaded kept in front of it (#244).
 *
 * Without this, every post-send refetch — which asks for the default newest
 * page — would silently collapse a thread the user had scrolled back through.
 * `has_more` then belongs to the oldest message on screen, not to the fresh
 * page, so the "load older" affordance stays correct.
 */
function mergeOlder(
  previous: ConversationDetail | null,
  fresh: ConversationDetail,
): ConversationDetail {
  if (previous === null || previous.messages.length === 0) return fresh
  const freshIds = new Set(fresh.messages.map((m) => m.id))
  const oldestFresh = fresh.messages[0]?.id
  const older = previous.messages.filter(
    (m) => !freshIds.has(m.id) && (oldestFresh === undefined || m.id < oldestFresh),
  )
  if (older.length === 0) return fresh
  return {
    ...fresh,
    messages: [...older, ...fresh.messages],
    has_more: previous.has_more,
  }
}

/**
 * One open conversation: history plus the send → loop → exchange round trip.
 *
 * The run is synchronous server-side (no streaming in v1), so `send` keeps an
 * optimistic user bubble + progress state up while it waits, then refetches
 * the thread from the server — the source of truth for what was persisted
 * (on failure the user turn may or may not have landed; the refetch shows
 * exactly what did). `onExchange` lets the page refresh the sidebar's
 * recency/title.
 */
export function useConversation(
  conversationId: number | null,
  onExchange?: () => void,
): UseConversation {
  const [loaded, setLoaded] = useState<Tagged<ConversationDetail> | null>(null)
  const [errorState, setErrorState] = useState<Tagged<string> | null>(null)
  const [pending, setPending] = useState<Tagged<string> | null>(null)
  const [olderPending, setOlderPending] = useState<Tagged<true> | null>(null)

  // The conversation the hook is currently routed to, readable from async
  // continuations that started before a navigation.
  const currentIdRef = useRef(conversationId)
  useEffect(() => {
    currentIdRef.current = conversationId
  }, [conversationId])

  const detail = forId(loaded, conversationId)
  const error = forId(errorState, conversationId)
  const pendingText = forId(pending, conversationId)
  const loadingOlder = forId(olderPending, conversationId) === true
  const loading = conversationId !== null && detail === null && error === null
  const hasMore = detail?.has_more ?? false

  useEffect(() => {
    if (conversationId === null) return
    let active = true
    getConversation(conversationId)
      .then((data) => {
        if (!active) return
        setLoaded({ id: conversationId, value: data })
        // A load that succeeds owns this conversation's outcome: drop the
        // failure it supersedes, or the recovered thread renders under a
        // stale alert (#233). Only this conversation's error, though — the
        // slot may hold one belonging to the thread the user came from.
        setErrorState((prev) => (prev !== null && prev.id === conversationId ? null : prev))
      })
      .catch((e: unknown) => {
        if (active) {
          setErrorState({
            id: conversationId,
            value: e instanceof Error ? e.message : 'Failed to load conversation',
          })
        }
      })
    return () => {
      active = false
    }
  }, [conversationId])

  const loadOlder = useCallback(async (): Promise<void> => {
    if (conversationId === null) return
    const oldest = loaded?.id === conversationId ? loaded.value.messages[0] : undefined
    if (oldest === undefined || !loaded?.value.has_more) return
    setOlderPending({ id: conversationId, value: true })
    try {
      const page = await getConversation(conversationId, { before_id: oldest.id })
      // Same single-slot guard as `send`: a page that lands after the user
      // navigated away must not touch the thread now on screen.
      if (currentIdRef.current === conversationId) {
        setLoaded((prev) =>
          prev !== null && prev.id === conversationId
            ? { id: conversationId, value: mergeOlder(page, prev.value) }
            : prev,
        )
      }
    } catch (e: unknown) {
      if (currentIdRef.current === conversationId) {
        setErrorState({
          id: conversationId,
          value: e instanceof Error ? e.message : 'Failed to load older messages',
        })
      }
    } finally {
      setOlderPending((prev) =>
        prev !== null && prev.id === conversationId ? null : prev,
      )
    }
  }, [conversationId, loaded])

  const send = useCallback(
    async (content: string): Promise<string | null> => {
      if (conversationId === null) return null
      setErrorState(null)
      setPending({ id: conversationId, value: content })
      let reply: string | null = null
      let committed: MessageExchange | null = null
      try {
        committed = await postMessage(conversationId, content)
        reply = committed.assistant_message.content ?? ''
      } catch (e: unknown) {
        if (currentIdRef.current === conversationId) {
          setErrorState({ id: conversationId, value: sendErrorMessage(e) })
        }
      }
      // Success or not, the server is the source of truth for the thread
      // (on failure the user turn may or may not have been persisted).
      try {
        const fresh = await getConversation(conversationId)
        // Only write if the user is still on this conversation — otherwise the
        // single slot would clobber whatever thread is on screen now. The
        // refetch asks for the newest page, so any older pages already loaded
        // are merged back in rather than collapsing the thread (#244).
        if (currentIdRef.current === conversationId) {
          setLoaded((prev) => ({
            id: conversationId,
            value: mergeOlder(prev !== null && prev.id === conversationId ? prev.value : null, fresh),
          }))
        }
      } catch (e: unknown) {
        // Same guard: a refetch that lost the race must not touch the thread
        // the user navigated to (#99).
        if (currentIdRef.current === conversationId) {
          if (committed !== null) {
            // The run landed and only the reload failed, so the exchange in
            // hand is the best available truth — without it the optimistic
            // bubble clears back to the pre-send thread and the turn looks
            // lost, inviting a duplicate send (#233). Merge only into this
            // conversation's own thread; there is nothing to append to when
            // the slot holds another id (or the initial load never landed),
            // and inventing a detail from two messages would hide the rest
            // of the history.
            const exchange = committed
            setLoaded((prev) =>
              prev !== null && prev.id === conversationId
                ? { id: conversationId, value: withExchange(prev.value, exchange) }
                : prev,
            )
            setErrorState({ id: conversationId, value: refreshErrorMessage(e) })
          }
          // Otherwise the send error is already surfaced and is the more
          // relevant one — a failed reload is a footnote to a failed run.
        }
      }
      setPending((prev) => (prev !== null && prev.id === conversationId ? null : prev))
      onExchange?.()
      return reply
    },
    [conversationId, onExchange],
  )

  return { detail, loading, error, hasMore, loadingOlder, loadOlder, pendingText, send }
}
