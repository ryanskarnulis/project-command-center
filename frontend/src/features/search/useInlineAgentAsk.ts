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
  /** Create a conversation and run one exchange. Resolves true on success. */
  ask: (text: string) => Promise<boolean>
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

  const ask = useCallback(async (text: string): Promise<boolean> => {
    setState({ phase: 'pending', text })
    try {
      const conversation = await createConversation()
      const exchange = await postMessage(conversation.id, text)
      setState({ phase: 'done', conversationId: conversation.id, exchange })
      return true
    } catch (e: unknown) {
      setState({ phase: 'error', text, message: sendErrorMessage(e) })
      return false
    }
  }, [])

  const dismiss = useCallback(() => {
    setState((s) => (s.phase === 'pending' ? s : { phase: 'idle' }))
  }, [])

  return { state, ask, dismiss }
}
