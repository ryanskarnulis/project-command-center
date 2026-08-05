import { apiClient } from './client'
import type {
  Conversation,
  ConversationDetail,
  MessageExchange,
} from '../types/agent'

// Posting a message runs the agent loop synchronously on the local GPU: warm
// runs are seconds, but a cold model load is ~100 s before the first token.
// This is the outermost ceiling and must stay above nginx (300 s) and the
// backend run budget (240 s) so the backend always returns a real error first
// rather than the browser aborting a still-valid run.
const AGENT_RUN_TIMEOUT_MS = 330_000

export async function listConversations(
  params: { limit?: number; offset?: number } = {},
): Promise<Conversation[]> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const suffix = query.toString() === '' ? '' : `?${query.toString()}`
  const res = await apiClient(`/api/agent/conversations${suffix}`)
  return (await res.json()) as Conversation[]
}

export async function createConversation(): Promise<Conversation> {
  const res = await apiClient('/api/agent/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  return (await res.json()) as Conversation
}

/** One page of a conversation: the newest messages by default, or the page
 * immediately older than `before_id`. The server caps `limit` (#244). */
export async function getConversation(
  id: number,
  params: { limit?: number; before_id?: number } = {},
): Promise<ConversationDetail> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.before_id !== undefined)
    query.set('before_id', String(params.before_id))
  const suffix = query.toString() === '' ? '' : `?${query.toString()}`
  const res = await apiClient(`/api/agent/conversations/${id}${suffix}`)
  return (await res.json()) as ConversationDetail
}

export async function deleteConversation(id: number): Promise<void> {
  await apiClient(`/api/agent/conversations/${id}`, { method: 'DELETE' })
}

export async function postMessage(
  conversationId: number,
  content: string,
): Promise<MessageExchange> {
  const res = await apiClient(
    `/api/agent/conversations/${conversationId}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
      timeoutMs: AGENT_RUN_TIMEOUT_MS,
    },
  )
  return (await res.json()) as MessageExchange
}
