import { useCallback, useEffect, useRef, useState } from 'react'
import { getConversation, postMessage } from '../../api/agent'
import type { ConversationDetail } from '../../types/agent'
import { sendErrorMessage } from './errorMessage'

interface UseConversation {
  detail: ConversationDetail | null
  loading: boolean
  error: string | null
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

  // The conversation the hook is currently routed to, readable from async
  // continuations that started before a navigation.
  const currentIdRef = useRef(conversationId)
  useEffect(() => {
    currentIdRef.current = conversationId
  }, [conversationId])

  const detail = forId(loaded, conversationId)
  const error = forId(errorState, conversationId)
  const pendingText = forId(pending, conversationId)
  const loading = conversationId !== null && detail === null && error === null

  useEffect(() => {
    if (conversationId === null) return
    let active = true
    getConversation(conversationId)
      .then((data) => {
        if (active) setLoaded({ id: conversationId, value: data })
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

  const send = useCallback(
    async (content: string): Promise<string | null> => {
      if (conversationId === null) return null
      setErrorState(null)
      setPending({ id: conversationId, value: content })
      let reply: string | null = null
      try {
        const exchange = await postMessage(conversationId, content)
        reply = exchange.assistant_message.content ?? ''
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
        // single slot would clobber whatever thread is on screen now.
        if (currentIdRef.current === conversationId) {
          setLoaded({ id: conversationId, value: fresh })
        }
      } catch {
        // The send error (if any) is already surfaced; keep it.
      }
      setPending((prev) => (prev !== null && prev.id === conversationId ? null : prev))
      onExchange?.()
      return reply
    },
    [conversationId, onExchange],
  )

  return { detail, loading, error, pendingText, send }
}
