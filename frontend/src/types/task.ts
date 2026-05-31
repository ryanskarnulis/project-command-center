export type TaskStatus = 'candidate' | 'accepted' | 'rejected' | 'done'
export type TaskPriority = 'low' | 'medium' | 'high' | 'urgent'

export interface Task {
  id: number
  project_id: number | null
  inbox_item_id: number | null
  title: string
  description: string | null
  status: TaskStatus
  priority: TaskPriority
  due_date: string | null
  confidence: number | null
  assignee_hint: string | null
  created_at: string
  updated_at: string
}

export interface TaskCreate {
  title: string
  description?: string | null
  status?: TaskStatus
  priority?: TaskPriority
  due_date?: string | null
}

export interface TaskUpdate {
  title?: string
  description?: string | null
  status?: TaskStatus
  priority?: TaskPriority
  due_date?: string | null
}
