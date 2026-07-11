import { ArrowRight } from 'lucide-react'
import { Card } from '../../components/Card'
import { MessageBubble } from '../agent/MessageBubble'
import { PendingExchange } from '../agent/PendingExchange'
import type { InlineAskState } from './useInlineAgentAsk'

interface InlineAgentExchangeProps {
  state: Exclude<InlineAskState, { phase: 'idle' }>
  /** Open the persisted conversation in the full agent panel. */
  onContinue: (conversationId: number) => void
}

/**
 * The panel under the command bar for one agent exchange. Rendering is the
 * agent feature's own surface — MessageBubble (markdown, tool-call links,
 * undo) and PendingExchange — inside the dropdown card, so the bar never
 * grows a second copy of message rendering.
 */
export function InlineAgentExchange({ state, onContinue }: InlineAgentExchangeProps) {
  return (
    <Card
      as="div"
      className="command-search-dropdown command-search-exchange"
      aria-label="Agent exchange"
    >
      <ul className="agent-messages">
        {state.phase === 'pending' && <PendingExchange text={state.text} />}
        {state.phase === 'error' && (
          <>
            <li className="agent-message agent-message--user">
              <div className="agent-bubble">{state.text}</div>
            </li>
            <li>
              <p role="alert" className="error command-search-exchange-error">
                {state.message}
              </p>
            </li>
          </>
        )}
        {state.phase === 'done' && (
          <>
            <MessageBubble message={state.exchange.user_message} />
            <MessageBubble message={state.exchange.assistant_message} />
          </>
        )}
      </ul>
      {state.phase === 'done' && (
        <div className="command-search-exchange-footer">
          <button
            type="button"
            className="command-search-continue"
            onClick={() => onContinue(state.conversationId)}
          >
            Continue in Agent
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      )}
    </Card>
  )
}
