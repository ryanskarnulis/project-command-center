export interface Project {
  id: number
  name: string
  description: string | null
  system_key: string | null
  is_protected: boolean
  created_at: string
  updated_at: string
}

export interface ProjectCreate {
  name: string
  description?: string | null
}

export interface ProjectUpdate {
  name?: string
  description?: string | null
}

export interface ProjectAlias {
  id: number
  project_id: number
  alias: string
  created_at: string
}

export interface ProjectAliasCreate {
  alias: string
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
