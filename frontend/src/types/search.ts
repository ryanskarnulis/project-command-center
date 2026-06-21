import type { TaskReviewStatus, TaskWorkflowStatus } from './task'

export type SearchKind = 'project' | 'task' | 'inbox'

export interface SearchResultItem {
  kind: SearchKind
  id: number
  title: string
  subtitle: string | null
  project_id: number | null
  // Populated only for the `task` kind (null for projects/inbox). Lets the command
  // bar's `/done` action offer only accepted, not-yet-done tasks.
  review_status: TaskReviewStatus | null
  workflow_status: TaskWorkflowStatus | null
}

export interface SearchResults {
  projects: SearchResultItem[]
  tasks: SearchResultItem[]
  inbox_items: SearchResultItem[]
}
