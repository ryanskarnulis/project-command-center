export interface ProjectOpenTasksRow {
  project_id: number
  project_name: string
  open_task_count: number
  /** Completed tasks in the project — the other half of the lane progress bar. */
  done_task_count: number
}

export interface DashboardOverview {
  total_open_tasks: number
  projects: ProjectOpenTasksRow[]
}
