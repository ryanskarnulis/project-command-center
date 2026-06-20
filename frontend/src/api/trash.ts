import { apiClient } from './client'
import type { EmptyTrashResult, Trash, TrashCountResult } from '../types/trash'

// The /trash list paginates (server caps at 200). We fetch the max so the page
// shows as much as possible; the unbounded /trash/count drives the true totals.
const TRASH_PAGE_LIMIT = 200

export async function getTrash(): Promise<Trash> {
  const res = await apiClient(`/api/trash?limit=${TRASH_PAGE_LIMIT}`)
  return (await res.json()) as Trash
}

export async function getTrashCount(): Promise<TrashCountResult> {
  const res = await apiClient('/api/trash/count')
  return (await res.json()) as TrashCountResult
}

export async function emptyTrash(): Promise<EmptyTrashResult> {
  const res = await apiClient('/api/trash', { method: 'DELETE' })
  return (await res.json()) as EmptyTrashResult
}
