// Mirrors backend/app/schemas/conversations.py.

export interface Conversation {
  id: number
  title: string | null
  created_at: string
  updated_at: string
}

/** One dispatched tool call persisted on an assistant message; exactly one of
 * result/error is set. Mirrors the backend's ToolCallRecord. */
export interface ToolCallRecord {
  tool: string
  arguments: Record<string, unknown>
  result: string | null
  error: string | null
}

export type AgentStopReason =
  | 'completed'
  | 'max_iterations'
  | 'correction_limit'
  // Failure stops: the run couldn't finish. The tool calls listed still ran
  // (and are undoable via the trash); the backend returns 502 / 504 for these.
  | 'provider_error'
  | 'timed_out'

export interface AgentMessage {
  id: number
  conversation_id: number
  role: 'user' | 'assistant'
  content: string | null
  tool_calls: ToolCallRecord[] | null
  stop_reason: AgentStopReason | null
  created_at: string
}

/** One bounded page of a conversation, oldest-first within the page.
 *
 * The backend returns the newest `limit` messages by default and pages
 * backwards with `before_id` (#244) — a thread's transcript grows without
 * limit, so no single response carries all of it. `has_more` says whether
 * anything older than `messages[0]` exists; `message_count` is the total. */
export interface ConversationDetail extends Conversation {
  messages: AgentMessage[]
  message_count: number
  has_more: boolean
}

export interface MessageExchange {
  user_message: AgentMessage
  assistant_message: AgentMessage
}
