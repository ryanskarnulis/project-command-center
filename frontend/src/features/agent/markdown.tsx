import type { Components } from 'react-markdown'
import type { AgentStopReason } from '../../types/agent'

// Shared by the desktop `MessageBubble` and the mobile `AgentTurn`; lives in
// its own module so each component file exports only components.

// react-markdown is safe by default (raw HTML never rendered). Links open in
// a new tab so a stray absolute URL in a reply can't navigate the SPA away.
export const MARKDOWN_COMPONENTS: Components = {
  a: ({ node, ...props }) => {
    void node // hast node isn't a DOM prop; strip it before spreading.
    return <a {...props} target="_blank" rel="noopener noreferrer" />
  },
}

export const STOP_FALLBACK: Record<Exclude<AgentStopReason, 'completed'>, string> = {
  max_iterations:
    'The agent hit its step limit before finishing — the tool calls above still ran.',
  correction_limit:
    'The agent kept producing invalid tool calls and gave up on this request.',
  provider_error:
    'The run failed partway — the tool calls above still ran (undo any from the trash).',
  timed_out:
    'The run ran out of time — the tool calls above still ran (undo any from the trash).',
}
