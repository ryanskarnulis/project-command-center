export interface ProjectOpenTasksRow {
  project_id: number
  project_name: string
  open_task_count: number
}

export interface DashboardOverview {
  total_open_tasks: number
  projects: ProjectOpenTasksRow[]
}
