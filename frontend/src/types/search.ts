export type SearchKind = 'project' | 'task' | 'inbox'

export interface SearchResultItem {
  kind: SearchKind
  id: number
  title: string
  subtitle: string | null
  project_id: number | null
}

export interface SearchResults {
  projects: SearchResultItem[]
  tasks: SearchResultItem[]
  inbox_items: SearchResultItem[]
}
