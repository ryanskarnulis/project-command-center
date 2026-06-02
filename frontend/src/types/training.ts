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
}

export interface TrainingStats {
  total: number
  accepted: number
  by_task: Record<string, number>
  goal: number
  remaining: number
}

export interface TrainingFilters {
  task_name?: string
  accepted?: boolean
}
