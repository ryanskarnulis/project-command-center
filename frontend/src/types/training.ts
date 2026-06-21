export interface TrainingExample {
  id: number
  task_name: string
  input_text: string
  model_output_json: string
  corrected_output_json: string | null
  accepted: boolean
  model_profile: string
  model_name: string
  created_at: string
  deleted_at: string | null
}

export interface TaskStat {
  count: number
  accepted: number
}

export interface TrainingStats {
  total: number
  accepted: number
  by_task: Record<string, TaskStat>
  profiles: string[]
  goal: number
  remaining: number
}

export type TrainingStatus = 'corrected' | 'accepted' | 'failure'

export interface TrainingFilters {
  task_name?: string
  status?: TrainingStatus
  model_profile?: string
  search?: string
}
