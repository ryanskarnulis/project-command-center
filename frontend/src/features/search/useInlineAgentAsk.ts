import { useCallback, useRef, useState } from 'react'
import { createConversation, postMessage } from '../../api/agent'
import { ApiError } from '../../api/client'
import { sendErrorMessage } from '../agent/errorMessage'
import type { MessageExchange } from '../../types/agent'

/** Lifecycle of one ask from the command bar. Single-exchange scoped: each
 * plain-Enter ask starts a fresh conversation; the previous one stays
 * reachable at /agent/:id.
 *
 * The error phase carries the conversation id whenever creation succeeded and
 * only the message failed, so the failure is recoverable rather than orphaned
 * (see the claim/adoption contract on the hook). */
export type InlineAskState =
  | { phase: 'idle' }
  | { phase: 'pending'; text: string }
  | { phase: 'done'; conversationId: number; exchange: MessageExchange }
  | { phase: 'error'; text: string; message: string; conversationId: number | null }

interface UseInlineAgentAsk {
  state: InlineAskState
  /** Create a conversation and run one exchange. Resolves to the assistant's
   * reply text on success (possibly ''), or null on failure — voice entry
   * uses the text to speak the reply. */
  ask: (text: string) => Promise<string | null>
  /** Close the inline exchange (the conversation stays persisted server-side).
   * No-op while a run is in flight — the working state stays visible until
   * the run lands, matching the panel's disabled-while-running contract. */
  dismiss: () => void
}

/**
 * Ambient agent entry for the command bar: posts the bar's text as the first
 * message of a NEW conversation and holds the resulting exchange for inline
 * rendering. The run is synchronous server-side (no streaming in v1), so the
 * pending state stays up for the whole round trip.
 *
 * Lifecycle contract for the two-step create-then-send workflow — the create
 * can succeed while the send fails, so the conversation is *claimed*, never
 * abandoned and never auto-deleted:
 *
 * 1. A conversation created for an ask stays claimed by this hook until a
 *    message lands in it.
 * 2. When the send fails, the id is kept in the error state (so the surface
 *    can offer "Continue in Agent" to inspect or delete it) *and* in a ref
 *    that outlives `dismiss` — typing in the bar dismisses the panel, and the
 *    retry must not lose the claim.
 * 3. The next ask reuses the claimed conversation instead of creating another,
 *    so N consecutive failures leave exactly one conversation, not N.
 * 4. Nothing is deleted automatically, not even on an unambiguous 429: a
 *    failed send is not proof the server persisted nothing (a network/timeout
 *    failure can follow a fully accepted message), and one branch that deletes
 *    while another does not is a worse contract than one that never deletes.
 *    Reuse in step 3 removes the accumulation the deletion would have fixed,
 *    and if the earlier turn *did* land, reusing shows it instead of hiding it.
 */
export function useInlineAgentAsk(): UseInlineAgentAsk {
  const [state, setState] = useState<InlineAskState>({ phase: 'idle' })
  // The claimed-but-unused conversation from a failed send, if any.
  const claimedIdRef = useRef<number | null>(null)

  const ask = useCallback(async (text: string): Promise<string | null> => {
    setState({ phase: 'pending', text })
    let conversationId = claimedIdRef.current
    try {
      if (conversationId === null) {
        conversationId = (await createConversation()).id
        claimedIdRef.current = conversationId
      }
      let exchange: MessageExchange
      try {
        exchange = await postMessage(conversationId, text)
      } catch (e: unknown) {
        // The claimed conversation can be deleted from the agent panel between
        // the failed ask and the retry; a 404 releases the claim and starts a
        // fresh one rather than failing every later ask.
        if (!(e instanceof ApiError && e.status === 404)) throw e
        claimedIdRef.current = null
        conversationId = (await createConversation()).id
        claimedIdRef.current = conversationId
        exchange = await postMessage(conversationId, text)
      }
      claimedIdRef.current = null
      setState({ phase: 'done', conversationId, exchange })
      return exchange.assistant_message.content ?? ''
    } catch (e: unknown) {
      setState({
        phase: 'error',
        text,
        message: sendErrorMessage(e),
        conversationId: claimedIdRef.current,
      })
      return null
    }
  }, [])

  const dismiss = useCallback(() => {
    setState((s) => (s.phase === 'pending' ? s : { phase: 'idle' }))
  }, [])

  return { state, ask, dismiss }
}
