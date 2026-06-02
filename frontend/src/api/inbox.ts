import { apiClient } from './client'
import type {
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

export async function listInbox(): Promise<InboxItem[]> {
  const res = await apiClient('/api/inbox')
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
  const res = await apiClient(`/api/inbox/${id}/process`, { method: 'POST' })
  return (await res.json()) as Task[]
}

export async function getCandidates(id: number): Promise<Task[]> {
  const res = await apiClient(`/api/inbox/${id}/candidates`)
  return (await res.json()) as Task[]
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
