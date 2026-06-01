export interface RecentInboxItem {
  id: number
  source: 'web' | 'discord'
  summary: string | null
  processed_at: string | null
  reviewed_at: string | null
  resolved_project_id: number | null
  created_at: string
}

export interface ProjectOpenTasksRow {
  project_id: number
  project_name: string
  open_task_count: number
}

export interface DashboardOverview {
  total_open_tasks: number
  projects: ProjectOpenTasksRow[]
  recent_inbox: RecentInboxItem[]
}

export interface ProjectSummary {
  project_id: number
  summary: string
  model_name: string
}
