import type { ReviewEdit } from '../../types/inbox'
import type { Task, TaskPriority } from '../../types/task'

/**
 * In-place edits to a candidate before the approve/dismiss decision.
 *
 * Override semantics (same pattern as the quick-add bar): `undefined` means
 * "untouched — show the candidate's own value". This keeps a draft correct
 * even when the baseline shifts under it (e.g. the projects list finishing
 * its load after the user already started typing a title).
 */
export interface CandidateDraft {
  title?: string
  description?: string | null
  due_date?: string | null
  priority?: TaskPriority
  assignee_hint?: string | null
  /** Chosen project id, or null for General. `undefined` = untouched. */
  project_id?: number | null
}

/** Empty string in a text input maps back to a null field on the backend. */
function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

/**
 * Build the ReviewEdit payload for an approve decision: only fields the user
 * touched AND that differ from what the backend would apply anyway.
 *
 * The project baseline is the note's suggestion or the General fallback (what
 * the backend applies when no override is sent), not the candidate's own
 * project_id, which is null pre-accept.
 */
export function diffCandidateDraft(
  candidate: Task,
  draft: CandidateDraft,
  effectiveProjectId: number | null,
): ReviewEdit | undefined {
  const edits: ReviewEdit = {}
  // An emptied title is never sent — the card disables Approve until it's back.
  if (
    draft.title !== undefined &&
    draft.title.trim() !== '' &&
    draft.title.trim() !== candidate.title
  ) {
    edits.title = draft.title.trim()
  }
  if (
    draft.description !== undefined &&
    orNull(draft.description ?? '') !== candidate.description
  ) {
    edits.description = orNull(draft.description ?? '')
  }
  if (draft.due_date !== undefined && draft.due_date !== candidate.due_date) {
    edits.due_date = draft.due_date
  }
  if (draft.priority !== undefined && draft.priority !== candidate.priority) {
    edits.priority = draft.priority
  }
  if (
    draft.assignee_hint !== undefined &&
    orNull(draft.assignee_hint ?? '') !== candidate.assignee_hint
  ) {
    edits.assignee_hint = orNull(draft.assignee_hint ?? '')
  }
  if (draft.project_id !== undefined && draft.project_id !== effectiveProjectId) {
    edits.project_id = draft.project_id
  }
  return Object.keys(edits).length > 0 ? edits : undefined
}
