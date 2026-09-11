import ReactMarkdown from 'react-markdown'
import { GlitchMark } from '../../components/GlitchMark'
import type { AgentMessage } from '../../types/agent'
import { formatRelative } from '../../utils/dates'
import { MARKDOWN_COMPONENTS, STOP_FALLBACK } from './markdown'
import { ToolCallList } from './ToolCallList'

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
        <GlitchMark size={20} />
      </span>
      <div className="agent-message-body">
        {message.tool_calls !== null && message.tool_calls.length > 0 && (
          <ToolCallList messageId={message.id} records={message.tool_calls} />
        )}
        {message.content !== null && (
          <div className="agent-bubble">
            <ReactMarkdown components={MARKDOWN_COMPONENTS}>
              {message.content}
            </ReactMarkdown>
          </div>
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
