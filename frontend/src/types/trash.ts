import type { Project } from './project'
import type { Task } from './task'

export interface TrashProject extends Project {
  /** Tasks cascade-deleted with this project that would return if restored with it. */
  archived_task_count: number
  /** Every trashed task this project owns — the set a purge permanently destroys. */
  purge_task_count: number
}

export interface Trash {
  projects: TrashProject[]
  tasks: Task[]
}

export interface EmptyTrashResult {
  projects: number
  tasks: number
}

export interface PurgeSelectedRequest {
  project_ids: number[]
  task_ids: number[]
}

/* Undo receipts (#306, #307). A restore is not the inverse of a delete — it may
 * bring back one row of a subtree, or rewind a recurring series and hand back a
 * different task — so the Trash page's Undo replays what the server says the
 * restore actually did, verbatim. The client never builds or edits these. */

export interface RescheduledDate {
  task_id: number
  due_date: string | null
}

export interface UnskipUndo {
  skipped_due_date: string
  /** The original skipped row, superseded by the un-skip and made skipped again by undo (#457). */
  superseded_task_id: number
  previous_due_dates: RescheduledDate[]
}

export interface TaskRestoreUndo {
  task_id: number
  /** The restore's own `restored` event: binds the receipt to that one restore (#313). */
  restore_event_id: number
  restored_task_ids: number[]
  skipped: boolean
  deleted_with_task_id: number | null
  unskip: UnskipUndo | null
}

export interface TaskRestoreResult {
  /** The task the restore handed back — for an un-skip, the rewound successor. */
  task: Task
  undo: TaskRestoreUndo
}

export interface ProjectRestoreUndo {
  project_id: number
  /**
   * The project's own `restored` event. The receipt names no tasks: the server
   * recomputes which rows the restore changed from that event (#314).
   */
  restore_event_id: number
}

export interface TrashCountResult {
  projects: number
  tasks: number
  /** Exact rows `DELETE /api/trash` removes (incl. cascade tasks, excl. protected projects). */
  purge_total: number
  /** Days after `deleted_at` that the server purges a trash row (#321); 0 = kept forever. */
  retention_days: number
}
