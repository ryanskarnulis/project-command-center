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
  // The user pressed Stop (#324). Not a failure — the POST returns 200 — but
  // like the failure stops, the tool calls listed still ran and are undoable.
  | 'cancelled'
  // Failure stops: the run couldn't finish. The tool calls listed still ran
  // (and are undoable via the trash); the backend returns 502 / 504 / 500.
  | 'provider_error'
  | 'timed_out'
  | 'internal_error'

/** Answer to a Stop request. `cancel_requested` is false when nothing was
 * running for that conversation (the run already landed, or had not started). */
export interface RunCancelResult {
  cancel_requested: boolean
}

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
  /** A run is in flight on this conversation right now (#358): its user turn
   * is already in `messages`, its reply is not. Lets a page that did not start
   * the run — a reload, another tab — show it, offer Stop, and poll. */
  run_in_flight: boolean
  /** That run's start (ISO, UTC); null when idle. */
  run_started_at: string | null
}

export interface MessageExchange {
  user_message: AgentMessage
  assistant_message: AgentMessage
}
