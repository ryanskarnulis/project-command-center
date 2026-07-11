import { Bot } from 'lucide-react'
import type { AgentMessage, AgentStopReason } from '../../types/agent'
import { formatRelative } from '../../utils/dates'
import { ToolCallList } from './ToolCallList'

const STOP_FALLBACK: Record<Exclude<AgentStopReason, 'completed'>, string> = {
  max_iterations:
    'The agent hit its step limit before finishing — the tool calls above still ran.',
  correction_limit:
    'The agent kept producing invalid tool calls and gave up on this request.',
}

export function MessageBubble({ message }: { message: AgentMessage }) {
  if (message.role === 'user') {
    return (
      <li className="agent-message agent-message--user">
        <div className="agent-bubble">{message.content}</div>
        <span className="agent-message-time">{formatRelative(message.created_at)}</span>
      </li>
    )
  }

  const stopReason = message.stop_reason
  return (
    <li className="agent-message agent-message--assistant">
      <span className="agent-avatar" aria-hidden="true">
        <Bot size={16} />
      </span>
      <div className="agent-message-body">
        {message.tool_calls !== null && message.tool_calls.length > 0 && (
          <ToolCallList messageId={message.id} records={message.tool_calls} />
        )}
        {message.content !== null && (
          <div className="agent-bubble">{message.content}</div>
        )}
        {stopReason !== null && stopReason !== 'completed' && (
          <p className="agent-stop-note" role="status">
            {STOP_FALLBACK[stopReason]}
          </p>
        )}
        <span className="agent-message-time">{formatRelative(message.created_at)}</span>
      </div>
    </li>
  )
}
