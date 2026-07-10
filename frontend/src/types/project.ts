export interface Project {
  id: number
  name: string
  description: string | null
  system_key: string | null
  sort_order: number
  is_protected: boolean
  created_at: string
  updated_at: string
  closed_at?: string | null
  deleted_at?: string | null
}

export interface ProjectCreate {
  name: string
  description?: string | null
}

export interface ProjectUpdate {
  name?: string
  description?: string | null
}

export interface ActivityEvent {
  id: number
  project_id: number | null
  entity_type: string
  entity_id: number
  action: string
  summary: string
  created_at: string
}
