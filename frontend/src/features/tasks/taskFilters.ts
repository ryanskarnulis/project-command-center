import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskWorkflowStatus } from '../../types/task'
import { compareTasks, dueStatus } from '../../utils/dates'

// The status dropdown doubles as a view selector: the three workflow states plus
// "Blocking"/"Blocked" (derived filters over active tasks) and "Done" (which
// swaps to the completed archive as its data source).
export type StatusView = '' | TaskWorkflowStatus | 'blocking' | 'blocked'

export interface Filters {
  search: string
  status: StatusView
  priority: TaskPriority | ''
  projectId: number | ''
  overdue: boolean
  dueSoon: boolean
}

export type SortMode = 'smart' | 'due_date' | 'priority' | 'project' | 'newest'

export type ViewMode = 'list' | 'board'

export const EMPTY_FILTERS: Filters = {
  search: '',
  status: '',
  priority: '',
  projectId: '',
  overdue: false,
  dueSoon: false,
}

const STATUS_VALUES: StatusView[] = [
  'open',
  'in_progress',
  'blocking',
  'blocked',
  'done',
]
const PRIORITY_VALUES: TaskPriority[] = ['urgent', 'high', 'medium', 'low']
const SORT_VALUES: SortMode[] = [
  'smart',
  'due_date',
  'priority',
  'project',
  'newest',
]

export function isTruthyParam(value: string | null): boolean {
  return value === '1' || value === 'true'
}

// Seed filter state from the URL query string so dashboard cards (and any other
// link) can deep-link into a pre-filtered view. Unknown/invalid values fall back
// to the empty defaults so a malformed URL can't produce an invalid filter state.
export function filtersFromParams(params: URLSearchParams): Filters {
  const status = params.get('status')
  const priority = params.get('priority')
  const project = params.get('project')
  const projectId = project !== null && /^\d+$/.test(project) ? Number(project) : ''
  return {
    search: params.get('search') ?? EMPTY_FILTERS.search,
    status: STATUS_VALUES.includes(status as StatusView)
      ? (status as StatusView)
      : EMPTY_FILTERS.status,
    priority: PRIORITY_VALUES.includes(priority as TaskPriority)
      ? (priority as TaskPriority)
      : EMPTY_FILTERS.priority,
    projectId,
    overdue: isTruthyParam(params.get('overdue')),
    dueSoon: isTruthyParam(params.get('dueSoon')),
  }
}

export function sortFromParams(params: URLSearchParams): SortMode {
  const sort = params.get('sort')
  return SORT_VALUES.includes(sort as SortMode) ? (sort as SortMode) : 'smart'
}

export function viewFromParams(
  params: URLSearchParams,
  defaultView: ViewMode = 'list',
): ViewMode {
  const view = params.get('view')
  if (view === 'board' || view === 'list') return view
  return defaultView
}

export function paramsFromState(
  filters: Filters,
  sortMode: SortMode,
  view: ViewMode,
  addingTask: boolean,
  defaultView: ViewMode = 'list',
): URLSearchParams {
  const params = new URLSearchParams()
  const search = filters.search.trim()
  if (search !== '') params.set('search', search)
  if (filters.status !== '') params.set('status', filters.status)
  if (filters.priority !== '') params.set('priority', filters.priority)
  if (filters.projectId !== '') params.set('project', String(filters.projectId))
  if (filters.overdue) params.set('overdue', '1')
  if (filters.dueSoon) params.set('dueSoon', '1')
  if (sortMode !== 'smart') params.set('sort', sortMode)
  if (view !== defaultView) params.set('view', view)
  if (addingTask) params.set('new', '1')
  return params
}

export function isActive(f: Filters): boolean {
  return (
    f.search.trim() !== '' ||
    f.status !== '' ||
    f.priority !== '' ||
    f.projectId !== '' ||
    f.overdue ||
    f.dueSoon
  )
}

export function matchesFilters(t: Task, f: Filters): boolean {
  const search = f.search.trim().toLowerCase()
  if (
    search &&
    !`${t.title} ${t.description ?? ''}`.toLowerCase().includes(search)
  ) {
    return false
  }
  // 'done' selects the completed data source, so it imposes no per-task status
  // check here; the workflow values and 'blocked' filter the active list.
  if (f.status === 'open' && t.workflow_status !== 'open') return false
  if (f.status === 'in_progress' && t.workflow_status !== 'in_progress') {
    return false
  }
  if (f.status === 'blocking' && !t.is_blocking) return false
  if (f.status === 'blocked' && !t.is_blocked) return false
  if (f.priority && t.priority !== f.priority) return false
  if (f.projectId !== '' && t.project_id !== f.projectId) return false
  // Overdue and Due soon combine as OR when both are set: a task passes the due
  // gate if it matches any enabled due predicate. (They describe mutually
  // exclusive states, so AND-ing them would always exclude everything.)
  const dueChecks: boolean[] = []
  if (f.overdue) dueChecks.push(dueStatus(t.due_date) === 'overdue')
  if (f.dueSoon) dueChecks.push(['today', 'soon'].includes(dueStatus(t.due_date)))
  if (dueChecks.length > 0 && !dueChecks.some(Boolean)) return false
  return true
}

function compareByDueDate(a: Task, b: Task): number {
  if (a.due_date === null && b.due_date === null) return compareTasks(a, b)
  if (a.due_date === null) return 1
  if (b.due_date === null) return -1
  return a.due_date.localeCompare(b.due_date) || compareTasks(a, b)
}

function compareByPriority(a: Task, b: Task): number {
  const rank: Record<TaskPriority, number> = {
    urgent: 0,
    high: 1,
    medium: 2,
    low: 3,
  }
  return rank[a.priority] - rank[b.priority] || compareTasks(a, b)
}

function compareByProject(a: Task, b: Task, projects: Project[]): number {
  const projectName = (t: Task) =>
    projects.find((p) => p.id === t.project_id)?.name ?? 'Unassigned'
  return projectName(a).localeCompare(projectName(b)) || compareTasks(a, b)
}

export function sortTasks(
  tasks: Task[],
  sortMode: SortMode,
  projects: Project[],
): Task[] {
  const copy = [...tasks]
  switch (sortMode) {
    case 'due_date':
      return copy.sort(compareByDueDate)
    case 'priority':
      return copy.sort(compareByPriority)
    case 'project':
      return copy.sort((a, b) => compareByProject(a, b, projects))
    case 'newest':
      return copy.sort(
        (a, b) =>
          b.created_at.localeCompare(a.created_at) ||
          b.id - a.id ||
          compareTasks(a, b),
      )
    case 'smart':
    default:
      return copy.sort(compareTasks)
  }
}
