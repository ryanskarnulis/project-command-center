import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Columns3,
  List,
  Plus,
  Search,
  SlidersHorizontal,
  Trash2,
} from 'lucide-react'
import { AsyncState } from '../../components/AsyncState'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type {
  Task,
  TaskCreate,
  TaskPriority,
  TaskWorkflowStatus,
} from '../../types/task'
import { compareTasks, dueStatus } from '../../utils/dates'
import { parseDurationInput } from '../../utils/duration'
import { ActivityFeed } from '../projects/ActivityFeed'
import { KanbanBoard } from './KanbanBoard'
import { TaskCard } from './TaskCard'
import { buildTaskTree } from './taskTree'
import { TaskFormModal } from './TaskFormModal'
import { useCompletedTasks } from './useCompletedTasks'
import { useTasks } from './useTasks'

// The status dropdown doubles as a view selector: the three workflow states plus
// "Blocking"/"Blocked" (derived filters over active tasks) and "Done" (which
// swaps to the completed archive as its data source).
type StatusView = '' | TaskWorkflowStatus | 'blocking' | 'blocked'

interface Filters {
  search: string
  status: StatusView
  priority: TaskPriority | ''
  projectId: number | ''
  overdue: boolean
  dueSoon: boolean
}

type SortMode = 'smart' | 'due_date' | 'priority' | 'project' | 'newest'

type ViewMode = 'list' | 'board'

const EMPTY_FILTERS: Filters = {
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

function isTruthyParam(value: string | null): boolean {
  return value === '1' || value === 'true'
}

// Seed filter state from the URL query string so dashboard cards (and any other
// link) can deep-link into a pre-filtered view. Unknown/invalid values fall back
// to the empty defaults so a malformed URL can't produce an invalid filter state.
function filtersFromParams(params: URLSearchParams): Filters {
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

function sortFromParams(params: URLSearchParams): SortMode {
  const sort = params.get('sort')
  return SORT_VALUES.includes(sort as SortMode) ? (sort as SortMode) : 'smart'
}

function viewFromParams(params: URLSearchParams): ViewMode {
  return params.get('view') === 'board' ? 'board' : 'list'
}

function paramsFromState(
  filters: Filters,
  sortMode: SortMode,
  view: ViewMode,
  addingTask: boolean,
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
  if (view === 'board') params.set('view', 'board')
  if (addingTask) params.set('new', '1')
  return params
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
  const { tasks, loading, error, create, update, markDone, remove, reload } =
    useTasks(id)

  const [searchParams, setSearchParams] = useSearchParams()
  // Board/list, filters, sorting, and the create-modal deep link are URL-backed
  // so links, refreshes, and browser back/forward restore the same task view.
  const view = viewFromParams(searchParams)
  const addingTask = isTruthyParam(searchParams.get('new'))
  const [projects, setProjects] = useState<Project[]>([])
  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams])

  // "Done" swaps the list to the completed archive (lazily fetched); the board
  // always needs it for its Done column.
  const showingCompleted = filters.status === 'done'
  const {
    tasks: completedTasks,
    loading: completedLoading,
    error: completedError,
    reopen,
    reload: reloadCompleted,
  } = useCompletedTasks(id, showingCompleted || view === 'board')
  const sortMode = sortFromParams(searchParams)
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<number>>(
    () => new Set(),
  )

  // Per-row subtask composer
  const EMPTY_SUBTASK_DRAFT = {
    title: '',
    priority: 'medium' as TaskPriority,
    dueDate: '',
    estimate: '',
  }
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)
  const [subtaskDraft, setSubtaskDraft] = useState(EMPTY_SUBTASK_DRAFT)
  const [subtaskError, setSubtaskError] = useState<string | null>(null)
  // "More options" hands the in-progress draft to the full task modal.
  const [subtaskModalDefaults, setSubtaskModalDefaults] =
    useState<Partial<TaskCreate> | null>(null)

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
    return buildTaskTree(filtered)
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

  // The board lays tasks out by workflow_status across three columns, so the
  // Status filter doesn't apply — keep every other filter, drop status.
  const boardFilters = useMemo(() => ({ ...filters, status: '' as const }), [
    filters,
  ])
  // The board is a flat layout with no nesting affordance, so it shows only
  // root tasks — subtasks (parent_task_id !== null) are excluded entirely.
  const boardActive = useMemo(() => {
    const source = isActive(boardFilters)
      ? tasks.filter((t) => matchesFilters(t, boardFilters))
      : tasks
    return source.filter((t) => t.parent_task_id === null)
  }, [tasks, boardFilters])
  const boardDone = useMemo(
    () =>
      completedTasks.filter(
        (t) => matchesFilters(t, boardFilters) && t.parent_task_id === null,
      ),
    [completedTasks, boardFilters],
  )

  // Route a board move to the right endpoint: Done uses the recurrence-safe
  // done endpoint, leaving Done uses reopen (→ open), everything else is a PATCH.
  async function handleSetStatus(t: Task, target: TaskWorkflowStatus) {
    if (target === 'done') {
      await markDone(t.id)
      reloadCompleted()
    } else if (t.workflow_status === 'done') {
      await reopen(t.id)
      if (target === 'in_progress') {
        await update(t.id, { workflow_status: 'in_progress' })
      } else {
        reload()
      }
    } else {
      await update(t.id, { workflow_status: target })
    }
    bumpActivity()
  }

  function updateTaskQuery(next: {
    filters?: Filters
    sortMode?: SortMode
    view?: ViewMode
    addingTask?: boolean
  }) {
    setSearchParams(
      paramsFromState(
        next.filters ?? filters,
        next.sortMode ?? sortMode,
        next.view ?? view,
        next.addingTask ?? addingTask,
      ),
      { replace: true },
    )
  }

  function selectView(next: ViewMode) {
    updateTaskQuery({ view: next })
  }

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

  function openSubtaskComposer(parent: Task) {
    // Seed priority/due date from the parent as overridable starting values.
    setSubtaskParentId(parent.id)
    setSubtaskDraft({
      ...EMPTY_SUBTASK_DRAFT,
      priority: parent.priority,
      dueDate: parent.due_date ?? '',
    })
    setSubtaskError(null)
  }

  function closeSubtaskComposer() {
    setSubtaskParentId(null)
    setSubtaskError(null)
  }

  async function handleAddSubtask(
    e: FormEvent<HTMLFormElement>,
    parentId: number,
  ) {
    e.preventDefault()
    if (!subtaskDraft.title.trim()) return
    const estimatedMinutes = parseDurationInput(subtaskDraft.estimate)
    if (estimatedMinutes === undefined) {
      setSubtaskError('Use something like 30m, 2h, or 1 day')
      return
    }
    await create({
      title: subtaskDraft.title.trim(),
      parent_task_id: parentId,
      priority: subtaskDraft.priority,
      due_date: subtaskDraft.dueDate || null,
      estimated_minutes: estimatedMinutes,
    })
    closeSubtaskComposer()
    bumpActivity()
  }

  // Hand the in-progress draft to the full task modal for the long-tail fields.
  function openSubtaskModal(parentId: number) {
    const estimatedMinutes = parseDurationInput(subtaskDraft.estimate)
    setSubtaskModalDefaults({
      parent_task_id: parentId,
      title: subtaskDraft.title.trim() || undefined,
      priority: subtaskDraft.priority,
      due_date: subtaskDraft.dueDate || null,
      // Drop an unparseable estimate rather than blocking the handoff; the modal
      // re-validates on save.
      estimated_minutes:
        estimatedMinutes === undefined ? null : estimatedMinutes,
    })
    closeSubtaskComposer()
  }

  function renderTask(t: Task) {
    const kids = sortTasks(childrenOf.get(t.id) ?? [], sortMode, projects)
    const isExpanded = expandedTaskIds.has(t.id)
    const actions = (
      <>
        <button
          className="task-action"
          onClick={() => openSubtaskComposer(t)}
        >
          <Plus size={16} aria-hidden="true" />
          Add subtask
        </button>
        {t.workflow_status !== 'done' && !t.has_subtasks && (
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
              value={subtaskDraft.title}
              onChange={(e) =>
                setSubtaskDraft((d) => ({ ...d, title: e.target.value }))
              }
              placeholder="Subtask title"
            />
            <div className="task-subtask-fields">
              <label>
                <span>Priority</span>
                <select
                  value={subtaskDraft.priority}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({
                      ...d,
                      priority: e.target.value as TaskPriority,
                    }))
                  }
                >
                  <option value="urgent">Urgent</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </label>
              <label>
                <span>Due date</span>
                <input
                  type="date"
                  value={subtaskDraft.dueDate}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({ ...d, dueDate: e.target.value }))
                  }
                />
              </label>
              <label>
                <span>Estimate</span>
                <input
                  placeholder="30m, 2h, 1 day"
                  value={subtaskDraft.estimate}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({ ...d, estimate: e.target.value }))
                  }
                />
              </label>
            </div>
            {subtaskError && <p role="alert">{subtaskError}</p>}
            <div className="task-subtask-actions">
              <button type="submit" disabled={!subtaskDraft.title.trim()}>
                Add
              </button>
              <button type="button" onClick={closeSubtaskComposer}>
                Cancel
              </button>
              <button
                type="button"
                className="secondary-action"
                onClick={() => openSubtaskModal(t.id)}
              >
                More options
              </button>
            </div>
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

      <div className="task-toolbar">
        <button type="button" onClick={() => updateTaskQuery({ addingTask: true })}>
          Add task
        </button>
        <div
          className="view-toggle"
          role="group"
          aria-label="View mode"
        >
          <button
            type="button"
            className={view === 'list' ? 'selected' : ''}
            aria-pressed={view === 'list'}
            onClick={() => selectView('list')}
          >
            <List size={16} aria-hidden="true" />
            List
          </button>
          <button
            type="button"
            className={view === 'board' ? 'selected' : ''}
            aria-pressed={view === 'board'}
            onClick={() => selectView('board')}
          >
            <Columns3 size={16} aria-hidden="true" />
            Board
          </button>
        </div>
      </div>

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
              onClick={() => updateTaskQuery({ filters: EMPTY_FILTERS })}
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
                updateTaskQuery({ filters: { ...filters, search: e.target.value } })
              }
              placeholder="Title or description"
            />
          </div>
        </label>

        <div className="task-filter-grid">
          {view !== 'board' && (
          <label>
            <span>Status</span>
            <select
              aria-label="Filter by status"
              value={filters.status}
              onChange={(e) =>
                updateTaskQuery({
                  filters: { ...filters, status: e.target.value as StatusView },
                })
              }
            >
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="in_progress">In progress</option>
              <option value="blocking">Blocking</option>
              <option value="blocked">Blocked</option>
              <option value="done">Done</option>
            </select>
          </label>
          )}

          <label>
            <span>Priority</span>
            <select
              aria-label="Filter by priority"
              value={filters.priority}
              onChange={(e) =>
                updateTaskQuery({
                  filters: {
                    ...filters,
                    priority: e.target.value as TaskPriority | '',
                  },
                })
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
                  updateTaskQuery({
                    filters: {
                      ...filters,
                      projectId:
                        e.target.value === '' ? '' : Number(e.target.value),
                    },
                  })
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

          {view !== 'board' && (
          <label>
            <span>Sort</span>
            <select
              aria-label="Sort tasks"
              value={sortMode}
              onChange={(e) =>
                updateTaskQuery({ sortMode: e.target.value as SortMode })
              }
            >
              <option value="smart">Smart order</option>
              <option value="due_date">Due date</option>
              <option value="priority">Priority</option>
              <option value="project">Project</option>
              <option value="newest">Newest</option>
            </select>
          </label>
          )}
        </div>

        <div className="task-filter-toggles" aria-label="Quick filters">
          <label className={filters.overdue ? 'selected' : ''}>
            <input
              type="checkbox"
              checked={filters.overdue}
              onChange={(e) =>
                updateTaskQuery({
                  filters: { ...filters, overdue: e.target.checked },
                })
              }
            />
            Overdue
          </label>

          <label className={filters.dueSoon ? 'selected' : ''}>
            <input
              type="checkbox"
              checked={filters.dueSoon}
              onChange={(e) =>
                updateTaskQuery({
                  filters: { ...filters, dueSoon: e.target.checked },
                })
              }
            />
            Due soon
          </label>
        </div>
      </div>

      {view === 'board' ? (
        <AsyncState
          loading={loading || completedLoading}
          error={error ?? completedError}
          isEmpty={boardActive.length === 0 && boardDone.length === 0}
          emptyLabel={
            filtersActive
              ? 'No tasks match the current filters.'
              : 'No tasks yet.'
          }
        >
          <KanbanBoard
            activeTasks={boardActive}
            completedTasks={boardDone}
            projects={projects}
            isGlobal={isGlobal}
            onSetStatus={handleSetStatus}
          />
        </AsyncState>
      ) : showingCompleted ? (
        <AsyncState
          loading={completedLoading}
          error={completedError}
          isEmpty={completedVisible.length === 0}
          emptyLabel={
            hasNonStatusFilters
              ? 'No completed tasks match the current filters.'
              : 'No completed tasks.'
          }
        >
          <ul className="task-list">
            {completedVisible.map(renderCompletedTask)}
          </ul>
        </AsyncState>
      ) : (
        <AsyncState
          loading={loading}
          error={error}
          isEmpty={roots.length === 0}
          emptyLabel={
            filtersActive
              ? 'No tasks match the current filters.'
              : 'No tasks yet.'
          }
        >
          <ul className="task-list">
            {sortTasks(roots, sortMode, projects).map(renderTask)}
          </ul>
        </AsyncState>
      )}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}

      {addingTask && (
        <TaskFormModal
          mode="create"
          tasks={tasks}
          projects={projects}
          onClose={() => updateTaskQuery({ addingTask: false })}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}

      {subtaskModalDefaults && (
        <TaskFormModal
          mode="create"
          defaults={subtaskModalDefaults}
          tasks={tasks}
          projects={projects}
          onClose={() => setSubtaskModalDefaults(null)}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}
    </main>
  )
}
