import type { TaskPriority } from './task'

export type InboxSource = 'web' | 'discord'
export type ReviewAction = 'accept' | 'reject'

export interface InboxItem {
  id: number
  raw_text: string
  input_hash: string
  source: InboxSource
  summary: string | null
  project_hint: string | null
  needs_review: boolean
  processed_at: string | null
  reviewed_at: string | null
  model_name: string | null
  suggested_project_id: number | null
  // The project alias the note matched on, when routed by an alias hit. Null for
  // name matches, the AI fallback, or no match.
  matched_alias: string | null
  created_at: string
  updated_at: string
  deleted_at?: string | null
}

export interface InboxCreate {
  raw_text: string
  source?: InboxSource
}

/** Per-task edits applied on accept. Only set fields are applied by the backend. */
export interface ReviewEdit {
  title?: string
  description?: string | null
  due_date?: string | null
  priority?: TaskPriority
  assignee_hint?: string | null
  /** Override the matched project: an id, or null to file under General. */
  project_id?: number | null
}

export interface ReviewDecision {
  task_id: number
  action: ReviewAction
  edits?: ReviewEdit
}

export interface ReviewRequest {
  decisions: ReviewDecision[]
}

export interface ReviewResult {
  accepted: number
  rejected: number
  training_example_id: number
  match_training_example_id: number | null
}

export interface CandidateDecision {
  action: 'approve' | 'dismiss'
  edits?: ReviewEdit
}

export interface CandidateResult {
  task_id: number
  action: 'approved' | 'dismissed'
  finalized: boolean
  training_example_id: number | null
  match_training_example_id: number | null
}
