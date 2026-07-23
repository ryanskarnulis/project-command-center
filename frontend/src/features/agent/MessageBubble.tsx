import ReactMarkdown, { type Components } from 'react-markdown'
import { SpiderMark } from '../../components/SpiderMark'
import type { AgentMessage, AgentStopReason } from '../../types/agent'
import { formatRelative } from '../../utils/dates'
import { ToolCallList } from './ToolCallList'

// react-markdown is safe by default (raw HTML never rendered). Links open in
// a new tab so a stray absolute URL in a reply can't navigate the SPA away.
const MARKDOWN_COMPONENTS: Components = {
  a: ({ node, ...props }) => {
    void node // hast node isn't a DOM prop; strip it before spreading.
    return <a {...props} target="_blank" rel="noopener noreferrer" />
  },
}

const STOP_FALLBACK: Record<Exclude<AgentStopReason, 'completed'>, string> = {
  max_iterations:
    'The agent hit its step limit before finishing — the tool calls above still ran.',
  correction_limit:
    'The agent kept producing invalid tool calls and gave up on this request.',
  provider_error:
    'The run failed partway — the tool calls above still ran (undo any from the trash).',
  timed_out:
    'The run ran out of time — the tool calls above still ran (undo any from the trash).',
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
        <SpiderMark size={16} />
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
