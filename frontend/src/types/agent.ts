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

export type AgentStopReason = 'completed' | 'max_iterations' | 'correction_limit'

export interface AgentMessage {
  id: number
  conversation_id: number
  role: 'user' | 'assistant'
  content: string | null
  tool_calls: ToolCallRecord[] | null
  stop_reason: AgentStopReason | null
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: AgentMessage[]
}

export interface MessageExchange {
  user_message: AgentMessage
  assistant_message: AgentMessage
}
