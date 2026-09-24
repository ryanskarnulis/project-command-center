import { apiClient } from './client'
import type {
  EmptyTrashResult,
  ProjectRestoreUndo,
  PurgeSelectedRequest,
  TaskRestoreResult,
  TaskRestoreUndo,
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

/** Restore one trashed task and get back the receipt that undoes exactly it.
 * `task` is not always the restored id: un-skipping a recurring occurrence
 * hands back the live successor it rewound (#306). */
export async function restoreTrashedTask(id: number): Promise<TaskRestoreResult> {
  return apiClient<TaskRestoreResult>(`/api/trash/tasks/${id}/restore`, { method: 'POST' })
}

/** Reverse one task restore from its receipt. 409 once the rows have moved on. */
export async function undoTaskRestore(undo: TaskRestoreUndo): Promise<void> {
  await apiClient('/api/trash/tasks/undo-restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(undo),
  })
}

/** Reverse one project restore from its receipt. 409 once the project has moved on. */
export async function undoProjectRestore(undo: ProjectRestoreUndo): Promise<void> {
  await apiClient('/api/trash/projects/undo-restore', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(undo),
  })
}
