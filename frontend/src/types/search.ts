import type { TaskWorkflowStatus } from './task'

export type SearchKind = 'project' | 'task'

export interface SearchResultItem {
  kind: SearchKind
  id: number
  title: string
  subtitle: string | null
  project_id: number | null
  // Populated only for the `task` kind (null for projects). Lets the command
  // bar's `/done` action offer only not-yet-done tasks.
  workflow_status: TaskWorkflowStatus | null
}

export interface SearchResults {
  projects: SearchResultItem[]
  tasks: SearchResultItem[]
}
