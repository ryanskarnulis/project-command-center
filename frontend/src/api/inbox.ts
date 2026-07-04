import { AI_TIMEOUT_MS, apiClient } from './client'
import type {
  CandidateDecision,
  CandidateResult,
  InboxCreate,
  InboxItem,
  ReviewRequest,
  ReviewResult,
} from '../types/inbox'
import type { Task } from '../types/task'

export async function createInbox(data: InboxCreate): Promise<InboxItem> {
  const res = await apiClient('/api/inbox', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as InboxItem
}

// The inbox list now caps server-side (default 200, max 500); request the max
// so the full inbox still loads for now.
const MAX_INBOX_LIMIT = 500

export async function listInbox(): Promise<InboxItem[]> {
  const res = await apiClient(`/api/inbox?limit=${MAX_INBOX_LIMIT}`)
  return (await res.json()) as InboxItem[]
}

export async function listPendingInbox(limit = 50): Promise<InboxItem[]> {
  const res = await apiClient(`/api/inbox/pending?limit=${limit}`)
  return (await res.json()) as InboxItem[]
}

export async function getInbox(id: number): Promise<InboxItem> {
  const res = await apiClient(`/api/inbox/${id}`)
  return (await res.json()) as InboxItem
}

export async function processInbox(id: number): Promise<Task[]> {
  const res = await apiClient(`/api/inbox/${id}/process`, {
    method: 'POST',
    timeoutMs: AI_TIMEOUT_MS,
  })
  return (await res.json()) as Task[]
}

export async function getCandidates(id: number): Promise<Task[]> {
  const res = await apiClient(`/api/inbox/${id}/candidates`)
  return (await res.json()) as Task[]
}

export async function dismissInbox(id: number): Promise<void> {
  await apiClient(`/api/inbox/${id}`, { method: 'DELETE' })
}

export async function restoreInbox(id: number): Promise<InboxItem> {
  const res = await apiClient(`/api/inbox/${id}/restore`, { method: 'POST' })
  return (await res.json()) as InboxItem
}

export async function purgeInbox(id: number): Promise<void> {
  await apiClient(`/api/inbox/${id}/purge`, { method: 'DELETE' })
}

export async function reviewInbox(
  id: number,
  data: ReviewRequest,
): Promise<ReviewResult> {
  const res = await apiClient(`/api/inbox/${id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as ReviewResult
}

export async function decideCandidate(
  inboxId: number,
  taskId: number,
  data: CandidateDecision,
): Promise<CandidateResult> {
  const res = await apiClient(`/api/inbox/${inboxId}/candidates/${taskId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return (await res.json()) as CandidateResult
}
