import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { AsyncState } from '../../components/AsyncState'
import type { Project } from '../../types/project'
import type { Task, TaskCreate, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { SubtaskComposer } from './SubtaskComposer'
import { TaskCard } from './TaskCard'
import {
  isActive,
  matchesFilters,
  sortTasks,
  type Filters,
  type SortMode,
} from './taskFilters'
import { buildTaskTree } from './taskTree'

interface TaskListViewProps {
  tasks: Task[]
  completedTasks: Task[]
  filters: Filters
  sortMode: SortMode
  projects: Project[]
  isGlobal: boolean
  showingCompleted: boolean
  loading: boolean
  error: string | null
  completedLoading: boolean
  completedError: string | null
  filtersActive: boolean
  hasNonStatusFilters: boolean
  create: (data: TaskCreate) => Promise<void>
  markDone: (id: number) => Promise<void>
  // Inline chip edits on a card; status routes through onSetStatus so done
  // transitions use the recurrence-safe endpoints.
  update: (task: Task, patch: TaskUpdate) => Promise<void>
  onSetStatus: (task: Task, target: TaskWorkflowStatus) => Promise<void>
  // Opens the skip-occurrence confirm for a recurring task (owned by the page).
  onSkip: (task: Task) => void
  remove: (id: number) => Promise<void>
  reopen: (id: number) => Promise<void>
  reload: () => void
  bumpActivity: () => void
  onOpenSubtaskModal: (defaults: Partial<TaskCreate>) => void
}

export function TaskListView({
  tasks,
  completedTasks,
  filters,
  sortMode,
  projects,
  isGlobal,
  showingCompleted,
  loading,
  error,
  completedLoading,
  completedError,
  filtersActive,
  hasNonStatusFilters,
  create,
  markDone,
  update,
  onSetStatus,
  onSkip,
  remove,
  reopen,
  reload,
  bumpActivity,
  onOpenSubtaskModal,
}: TaskListViewProps) {
  const [expandedTaskIds, setExpandedTaskIds] = useState<Set<number>>(
    () => new Set(),
  )
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)

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

  async function handleCreateSubtask(data: TaskCreate) {
    await create(data)
    bumpActivity()
    setSubtaskParentId(null)
  }

  function renderTask(t: Task) {
    const kids = sortTasks(childrenOf.get(t.id) ?? [], sortMode, projects)
    const isExpanded = expandedTaskIds.has(t.id)
    const actions = (
      <>
        <button
          className="task-action"
          onClick={() => setSubtaskParentId(t.id)}
        >
          <Plus size={16} aria-hidden="true" />
          Add subtask
        </button>
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
          onComplete={() => void markDone(t.id).then(bumpActivity)}
          onUpdate={(patch) => void update(t, patch)}
          onSetStatus={(target) => void onSetStatus(t, target)}
          onSkipOccurrence={() => onSkip(t)}
        />
        {subtaskParentId === t.id && (
          <SubtaskComposer
            key={t.id}
            parent={t}
            onCreate={handleCreateSubtask}
            onMoreOptions={(defaults) => {
              onOpenSubtaskModal(defaults)
              setSubtaskParentId(null)
            }}
            onCancel={() => setSubtaskParentId(null)}
          />
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
          onUpdate={(patch) => void update(t, patch)}
          onSetStatus={(target) => void onSetStatus(t, target)}
        />
      </li>
    )
  }

  if (showingCompleted) {
    return (
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
        <ul className="task-list">{completedVisible.map(renderCompletedTask)}</ul>
      </AsyncState>
    )
  }

  return (
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
  )
}
