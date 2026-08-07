import { apiClient } from './client'
import type {
  EmptyTrashResult,
  PurgeSelectedRequest,
  Trash,
  TrashCountResult,
} from '../types/trash'

// The /trash list paginates (server caps at 200). We fetch the max so the page
// shows as much as possible; the unbounded /trash/count drives the true totals.
const TRASH_PAGE_LIMIT = 200

export async function getTrash(): Promise<Trash> {
  return apiClient<Trash>(`/api/trash?limit=${TRASH_PAGE_LIMIT}`)
}

export async function getTrashCount(): Promise<TrashCountResult> {
  return apiClient<TrashCountResult>('/api/trash/count')
}

/** Permanently delete the selected trashed rows. Ids already gone are skipped. */
export async function purgeSelected(
  data: PurgeSelectedRequest,
): Promise<EmptyTrashResult> {
  return apiClient<EmptyTrashResult>('/api/trash/purge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function emptyTrash(): Promise<EmptyTrashResult> {
  return apiClient<EmptyTrashResult>('/api/trash', { method: 'DELETE' })
}
