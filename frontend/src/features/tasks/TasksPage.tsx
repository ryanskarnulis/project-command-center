import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Plus,
  Search,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskWorkflowStatus } from '../../types/task'
import { compareTasks, dueStatus } from '../../utils/dates'
import { ActivityFeed } from '../projects/ActivityFeed'
import { TaskCard } from './TaskCard'
import { TaskFormModal } from './TaskFormModal'
import { useCompletedTasks } from './useCompletedTasks'
import { useTasks } from './useTasks'

// The status dropdown doubles as a view selector: the three workflow states plus
// "Blocked" (a derived filter over active tasks) and "Done" (which swaps to the
// completed archive as its data source).
type StatusView = '' | TaskWorkflowStatus | 'blocked'

interface Filters {
  search: string
  status: StatusView
  priority: TaskPriority | ''
  projectId: number | ''
  overdue: boolean
  dueSoon: boolean
}

type SortMode = 'smart' | 'due_date' | 'priority' | 'project' | 'newest'

const EMPTY_FILTERS: Filters = {
  search: '',
  status: '',
  priority: '',
  projectId: '',
  overdue: false,
  dueSoon: false,
}

function isActive(f: Filters): boolean {
  return (
    f.search.trim() !== '' ||
    f.status !== '' ||
    f.priority !== '' ||
    f.projectId !== '' ||
    f.overdue ||
    f.dueSoon
  )
}

function matchesFilters(t: Task, f: Filters): boolean {
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
  if (f.status === 'blocked' && !t.is_blocked) return false
  if (f.priority && t.priority !== f.priority) return false
  if (f.projectId !== '' && t.project_id !== f.projectId) return false
  if (f.overdue && dueStatus(t.due_date) !== 'overdue') return false
  if (f.dueSoon && !['today', 'soon'].includes(dueStatus(t.due_date))) {
    return false
  }
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

function sortTasks(
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

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, markDone, remove, reload } =
    useTasks(id)

  const [addingTask, setAddingTask] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)

  // "Done" swaps the displayed list to the completed archive (lazily fetched).
  const showingCompleted = filters.status === 'done'
  const {
    tasks: completedTasks,
    loading: completedLoading,
    error: completedError,
    reopen,
  } = useCompletedTasks(id, showingCompleted)
  const [sortMode, setSortMode] = useState<SortMode>('smart')
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<number>>(
    () => new Set(),
  )

  // Per-row subtask composer
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)
  const [subtaskTitle, setSubtaskTitle] = useState('')

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {})
  }, [])
  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  // Filter flat list first, then build tree. A child that matches while its parent
  // doesn't is promoted to root — same behaviour as orphaned-parent fallback.
  const { roots, childrenOf } = useMemo(() => {
    const filtered = isActive(filters)
      ? tasks.filter((t) => matchesFilters(t, filters))
      : tasks
    const ids = new Set(filtered.map((t) => t.id))
    const childrenOf = new Map<number, Task[]>()
    const roots: Task[] = []
    for (const t of filtered) {
      if (t.parent_task_id !== null && ids.has(t.parent_task_id)) {
        const group = childrenOf.get(t.parent_task_id) ?? []
        group.push(t)
        childrenOf.set(t.parent_task_id, group)
      } else {
        roots.push(t)
      }
    }
    return { roots, childrenOf }
  }, [tasks, filters])

  // Completed tasks are a flat archive — no tree, just filter + sort.
  const completedVisible = useMemo(
    () =>
      sortTasks(
        completedTasks.filter((t) => matchesFilters(t, filters)),
        sortMode,
        projects,
      ),
    [completedTasks, filters, sortMode, projects],
  )

  const filtersActive = isActive(filters)
  const hasNonStatusFilters =
    filters.search.trim() !== '' ||
    filters.priority !== '' ||
    filters.projectId !== '' ||
    filters.overdue ||
    filters.dueSoon
  const activeFilterCount = [
    filters.search.trim() !== '',
    filters.status !== '',
    filters.priority !== '',
    filters.projectId !== '',
    filters.overdue,
    filters.dueSoon,
  ].filter(Boolean).length

  function toggleExpanded(taskId: number) {
    setExpandedTaskIds((current) => {
      const next = new Set(current)
      if (next.has(taskId)) {
        next.delete(taskId)
      } else {
        next.add(taskId)
      }
      return next
    })
  }

  async function handleAddSubtask(e: FormEvent<HTMLFormElement>, parentId: number) {
    e.preventDefault()
    if (!subtaskTitle.trim()) return
    await create({ title: subtaskTitle.trim(), parent_task_id: parentId })
    setSubtaskTitle('')
    setSubtaskParentId(null)
    bumpActivity()
  }

  function renderTask(t: Task) {
    const kids = sortTasks(childrenOf.get(t.id) ?? [], sortMode, projects)
    const isExpanded = expandedTaskIds.has(t.id)
    const actions = (
      <>
        <button
          className="task-action"
          onClick={() => {
            setSubtaskParentId(t.id)
            setSubtaskTitle('')
          }}
        >
          <Plus size={16} aria-hidden="true" />
          Add subtask
        </button>
        {t.workflow_status !== 'done' && (
          <button
            className="task-icon-action"
            aria-label={`Mark ${t.title} done`}
            title="Mark done"
            onClick={() => void markDone(t.id).then(bumpActivity)}
          >
            <Check size={17} aria-hidden="true" />
          </button>
        )}
        <button
          className="task-icon-action danger-action"
          aria-label={`Delete ${t.title}`}
          title="Delete"
          onClick={() => void remove(t.id).then(bumpActivity)}
        >
          <Trash2 size={17} aria-hidden="true" />
        </button>
      </>
    )
    return (
      <li key={t.id}>
        <TaskCard
          task={t}
          projects={isGlobal ? projects : undefined}
          actions={actions}
        />
        {subtaskParentId === t.id && (
          <form
            className="task-subtask-form"
            onSubmit={(e) => void handleAddSubtask(e, t.id)}
          >
            <input
              autoFocus
              value={subtaskTitle}
              onChange={(e) => setSubtaskTitle(e.target.value)}
              placeholder="Subtask title"
            />
            <button type="submit" disabled={!subtaskTitle.trim()}>
              Add
            </button>{' '}
            <button type="button" onClick={() => setSubtaskParentId(null)}>
              Cancel
            </button>
          </form>
        )}
        {kids.length > 0 && (
          <div className="task-subtasks">
            <button
              type="button"
              className="task-subtasks-toggle"
              aria-expanded={isExpanded}
              onClick={() => toggleExpanded(t.id)}
            >
              {isExpanded ? (
                <ChevronDown size={16} aria-hidden="true" />
              ) : (
                <ChevronRight size={16} aria-hidden="true" />
              )}
              <span>Subtasks ({kids.length})</span>
            </button>
            {isExpanded && (
              <ul className="task-children">{kids.map(renderTask)}</ul>
            )}
          </div>
        )}
      </li>
    )
  }

  function renderCompletedTask(t: Task) {
    // Reopen pulls the task out of the archive and back into the active list;
    // reload() refreshes that list so it's there when the view switches back.
    const actions = (
      <button
        className="task-action"
        onClick={() => void reopen(t.id).then(reload)}
      >
        Reopen
      </button>
    )
    return (
      <li key={t.id}>
        <TaskCard
          task={t}
          projects={isGlobal ? projects : undefined}
          actions={actions}
        />
      </li>
    )
  }

  return (
    <main>
      {!isGlobal && (
        <p>
          <Link to="/projects">← Projects</Link>
        </p>
      )}
      <h1>{isGlobal ? 'Open Tasks' : 'Tasks'}</h1>

      <button type="button" onClick={() => setAddingTask(true)}>
        Add task
      </button>

      <div className="task-filters" role="search" aria-label="Filter tasks">
        <div className="task-filters-header">
          <div className="task-filters-title">
            <SlidersHorizontal size={17} aria-hidden="true" />
            <strong>Filters</strong>
            {activeFilterCount > 0 && (
              <span className="count-badge">{activeFilterCount} active</span>
            )}
          </div>
          {filtersActive && (
            <button
              type="button"
              className="secondary-action"
              onClick={() => setFilters(EMPTY_FILTERS)}
            >
              Clear filters
            </button>
          )}
        </div>

        <label className="task-search-field">
          <span>Search</span>
          <div>
            <Search size={17} aria-hidden="true" />
            <input
              aria-label="Search tasks"
              value={filters.search}
              onChange={(e) =>
                setFilters((f) => ({ ...f, search: e.target.value }))
              }
              placeholder="Title or description"
            />
          </div>
        </label>

        <div className="task-filter-grid">
          <label>
            <span>Status</span>
            <select
              aria-label="Filter by status"
              value={filters.status}
              onChange={(e) =>
                setFilters((f) => ({
                  ...f,
                  status: e.target.value as StatusView,
                }))
              }
            >
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="blocked">Blocked</option>
              <option value="done">Done</option>
            </select>
          </label>

          <label>
            <span>Priority</span>
            <select
              aria-label="Filter by priority"
              value={filters.priority}
              onChange={(e) =>
                setFilters((f) => ({
                  ...f,
                  priority: e.target.value as TaskPriority | '',
                }))
              }
            >
              <option value="">All priorities</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>

          {isGlobal && (
            <label>
              <span>Project</span>
              <select
                aria-label="Filter by project"
                value={filters.projectId === '' ? '' : String(filters.projectId)}
                onChange={(e) =>
                  setFilters((f) => ({
                    ...f,
                    projectId:
                      e.target.value === '' ? '' : Number(e.target.value),
                  }))
                }
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <label>
            <span>Sort</span>
            <select
              aria-label="Sort tasks"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
            >
              <option value="smart">Smart order</option>
              <option value="due_date">Due date</option>
              <option value="priority">Priority</option>
              <option value="project">Project</option>
              <option value="newest">Newest</option>
            </select>
          </label>
        </div>

        <div className="task-filter-toggles" aria-label="Quick filters">
          <label className={filters.overdue ? 'selected' : ''}>
            <input
              type="checkbox"
              checked={filters.overdue}
              onChange={(e) =>
                setFilters((f) => ({ ...f, overdue: e.target.checked }))
              }
            />
            Overdue
          </label>

          <label className={filters.dueSoon ? 'selected' : ''}>
            <input
              type="checkbox"
              checked={filters.dueSoon}
              onChange={(e) =>
                setFilters((f) => ({ ...f, dueSoon: e.target.checked }))
              }
            />
            Due soon
          </label>
        </div>
      </div>

      {showingCompleted ? (
        <>
          {completedLoading && <p>Loading…</p>}
          {completedError && <p role="alert">{completedError}</p>}

          <ul className="task-list">
            {completedVisible.map(renderCompletedTask)}
          </ul>

          {!completedLoading && completedVisible.length === 0 && (
            <p>
              {hasNonStatusFilters
                ? 'No completed tasks match the current filters.'
                : 'No completed tasks.'}
            </p>
          )}
        </>
      ) : (
        <>
          {loading && <p>Loading…</p>}
          {error && <p role="alert">{error}</p>}

          <ul className="task-list">
            {sortTasks(roots, sortMode, projects).map(renderTask)}
          </ul>

          {!loading && roots.length === 0 && (
            <p>
              {filtersActive
                ? 'No tasks match the current filters.'
                : 'No tasks yet.'}
            </p>
          )}
        </>
      )}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}

      {addingTask && (
        <TaskFormModal
          mode="create"
          tasks={tasks}
          projects={projects}
          onClose={() => setAddingTask(false)}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}
    </main>
  )
}
