import type { InboxItem } from './inbox'
import type { Project } from './project'
import type { Task } from './task'

export interface Trash {
  projects: Project[]
  tasks: Task[]
  inbox_items: InboxItem[]
}
