import { useCallback, useState } from 'react'
import { createConversation, postMessage } from '../../api/agent'
import { sendErrorMessage } from '../agent/useConversation'
import type { MessageExchange } from '../../types/agent'

/** Lifecycle of one ask from the command bar. Single-exchange scoped: each
 * plain-Enter ask starts a fresh conversation; the previous one stays
 * reachable at /agent/:id. */
export type InlineAskState =
  | { phase: 'idle' }
  | { phase: 'pending'; text: string }
  | { phase: 'done'; conversationId: number; exchange: MessageExchange }
  | { phase: 'error'; text: string; message: string }

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
 */
export function useInlineAgentAsk(): UseInlineAgentAsk {
  const [state, setState] = useState<InlineAskState>({ phase: 'idle' })

  const ask = useCallback(async (text: string): Promise<string | null> => {
    setState({ phase: 'pending', text })
    try {
      const conversation = await createConversation()
      const exchange = await postMessage(conversation.id, text)
      setState({ phase: 'done', conversationId: conversation.id, exchange })
      return exchange.assistant_message.content ?? ''
    } catch (e: unknown) {
      setState({ phase: 'error', text, message: sendErrorMessage(e) })
      return null
    }
  }, [])

  const dismiss = useCallback(() => {
    setState((s) => (s.phase === 'pending' ? s : { phase: 'idle' }))
  }, [])

  return { state, ask, dismiss }
}
