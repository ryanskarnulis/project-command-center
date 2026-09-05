import { Fragment, useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { Check, ChevronDown, ChevronRight, Eye, Plus, SlidersHorizontal, X } from 'lucide-react'
import type { Project } from '../../../types/project'
import type { Task, TaskWorkflowStatus } from '../../../types/task'
import { ActivityFeed } from '../../projects/ActivityFeed'
import { ProjectTabs } from '../../projects/ProjectTabs'
import { TaskCard } from '../TaskCard'
import { EMPTY_FILTERS, sortTasks, type Filters, type SortMode } from '../taskFilters'
import { buildTaskTree, isEffectiveTopLevel } from '../taskTree'
import { subtaskMoveRefusal } from '../taskStatusRules'
import { matchingMobileTasks, mobileFilterChips } from './mobileTaskFilters'
import { TaskFilterSheet } from './TaskFilterSheet'

interface Props {
  projectId: number
  projects: Project[]
  tasks: Task[]
  completedTasks: Task[]
  completedLoading: boolean
  completedError: string | null
  loading: boolean
  error: string | null
  filters: Filters
  sortMode: SortMode
  activityKey: number
  updateQuery: (next: { filters?: Filters; sortMode?: SortMode; addingTask?: boolean }) => void
  onSetStatus: (task: Task, status: TaskWorkflowStatus) => Promise<void>
}

function savedExpansion(projectId: number): Set<number> {
  try {
    const ids: unknown = JSON.parse(sessionStorage.getItem(`pcc:task-expansion:${projectId}`) ?? '[]')
    return new Set(Array.isArray(ids) ? ids.filter((id): id is number => typeof id === 'number') : [])
  } catch { return new Set() }
}

export function MobileProjectTasks({ projectId, projects, tasks, completedTasks, completedLoading, completedError, loading, error, filters, sortMode, activityKey, updateQuery, onSetStatus }: Props) {
  const [showDone, setShowDone] = useState(false)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [expanded, setExpanded] = useState(() => savedExpansion(projectId))
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set())
  const [mutationError, setMutationError] = useState<string | null>(null)
  const projectName = projects.find((project) => project.id === projectId)?.name ?? 'Project'
  // A same-scope refresh can briefly leave an id in both hook snapshots.
  const allTasks = useMemo(() => [...new Map([...completedTasks, ...tasks].map((task) => [task.id, task])).values()], [tasks, completedTasks])
  const visible = sortTasks(matchingMobileTasks(allTasks, filters, showDone), sortMode, projects)
  const tree = buildTaskTree(visible)
  const allChildren = buildTaskTree(allTasks).childrenOf
  const chips = mobileFilterChips(filters, sortMode)
  const openRoots = tasks.filter(isEffectiveTopLevel).length
  const subtasks = tasks.length - openRoots
  // Counted off the deduped union, not the two raw snapshots: an id present in
  // both mid-refresh would otherwise inflate the denominator and dip the bar.
  const doneCount = allTasks.filter((task) => task.workflow_status === 'done').length
  const percent = allTasks.length ? Math.round(doneCount / allTasks.length * 100) : 0
  const doneVisible = showDone || filters.status === 'done'

  function toggleExpanded(id: number): void {
    const next = new Set(expanded)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setExpanded(next)
    try { sessionStorage.setItem(`pcc:task-expansion:${projectId}`, JSON.stringify([...next])) } catch { /* Disclosure still works when storage is unavailable. */ }
  }

  async function complete(task: Task): Promise<void> {
    setPendingIds((ids) => new Set(ids).add(task.id))
    setMutationError(null)
    try {
      await onSetStatus(task, task.workflow_status === 'done' ? 'open' : 'done')
    } catch (reason) {
      setMutationError(reason instanceof Error ? reason.message : 'Unable to update task')
    } finally {
      setPendingIds((ids) => { const next = new Set(ids); next.delete(task.id); return next })
    }
  }

  function renderTask(task: Task, depth = 0): ReactNode {
    const children = tree.childrenOf.get(task.id) ?? []
    const count = allChildren.get(task.id)?.length ?? 0
    const isDone = task.workflow_status === 'done'
    const refusal = subtaskMoveRefusal(task, isDone ? 'open' : 'done') ?? (!isDone && task.is_blocked ? 'Blocked by an unfinished dependency' : null)
    return (
      <Fragment key={task.id}>
        <li className={`mobile-task-row workflow-${task.workflow_status}`} style={{ '--task-indent': `${Math.min(depth, 4) * 30}px` } as CSSProperties}>
          <button type="button" className="task-complete-circle" disabled={!!refusal || pendingIds.has(task.id)} title={refusal ?? (isDone ? 'Reopen task' : 'Mark done')} aria-label={isDone ? `Reopen ${task.title}` : `Mark ${task.title} done`} onClick={() => void complete(task)}>
            <Check size={13} aria-hidden="true" />
          </button>
          <TaskCard task={task} dense subtaskCount={count} />
          {children.length > 0 && (
            <button type="button" className="mobile-subtask-toggle" aria-label={`${expanded.has(task.id) ? 'Collapse' : 'Expand'} subtasks of ${task.title}`} aria-expanded={expanded.has(task.id)} onClick={() => toggleExpanded(task.id)}>
              {expanded.has(task.id) ? <ChevronDown size={18} aria-hidden="true" /> : <ChevronRight size={18} aria-hidden="true" />}
            </button>
          )}
        </li>
        {expanded.has(task.id) && children.map((child) => renderTask(child, depth + 1))}
      </Fragment>
    )
  }

  return (
    <>
      <header className="mobile-tasks-heading">
        <div className="mobile-project-title">
          <h1 title={projectName}>{projectName}</h1>
          <div className="mobile-project-progress">
            <span className="dashboard-lane-progress" role="progressbar" aria-label={`${projectName} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={completedLoading || completedError ? undefined : percent}>
              <span style={{ width: `${percent}%` }} />
            </span>
            <span>{loading ? 'Loading tasks…' : `${openRoots} open · ${subtasks} subtasks`}{!completedLoading && !completedError && ` · ${doneCount} done`}</span>
          </div>
        </div>
        <button type="button" className={`mobile-filter-control${chips.length ? ' applied' : ''}`} aria-label="Filter tasks" aria-haspopup="dialog" aria-expanded={sheetOpen} onClick={() => setSheetOpen(true)}>
          <SlidersHorizontal size={18} aria-hidden="true" />
        </button>
      </header>
      <ProjectTabs projectId={projectId} />
      {chips.length > 0 && (
        <div className="mobile-filter-chips" aria-label="Applied filters">
          {chips.map(({ label, ...next }) => <button type="button" key={label} aria-label={`Remove ${label}`} onClick={() => updateQuery(next)}>{label}<X size={13} aria-hidden="true" /></button>)}
          <button type="button" className="mobile-filters-clear" onClick={() => updateQuery({ filters: EMPTY_FILTERS, sortMode: 'smart' })}>Clear</button>
        </div>
      )}
      <div className="mobile-task-list">
        <button type="button" className="mobile-add-task" onClick={() => updateQuery({ addingTask: true })}><span><Plus size={12} aria-hidden="true" /></span>Add task</button>
        {loading ? <p>Loading tasks…</p> : error ? <p role="alert">{error}</p> : (
          <>
            {(['open', 'in_progress', 'done'] as const).map((status) => {
              const roots = tree.roots.filter((task) => task.workflow_status === status)
              if (status === 'done' && !doneVisible) return null
              if (!roots.length) return null
              const label = status === 'open' ? 'Open' : status === 'in_progress' ? 'In progress' : 'Done'
              return <section key={status} className={`mobile-task-group group-${status}`} aria-label={label}>
                <h2><span>{label}</span><span className="mobile-group-count">{roots.length}</span></h2>
                <ul>{roots.map((task) => renderTask(task))}</ul>
              </section>
            })}
            {visible.length === 0 && <p className="mobile-tasks-empty">{chips.length ? 'No matching tasks' : 'No open tasks'}</p>}
          </>
        )}
        {completedError && <p role="alert">{completedError}</p>}
        {mutationError && <p role="alert">{mutationError}</p>}
        <div className="mobile-task-foot">
          <button type="button" className="mobile-show-done" aria-expanded={doneVisible} onClick={() => {
            if (filters.status === 'done') updateQuery({ filters: { ...filters, status: '' } })
            setShowDone(!doneVisible)
          }}><Eye size={16} aria-hidden="true" />{doneVisible ? 'Hide done' : 'Show done'}{!completedLoading && !completedError && ` · ${doneCount}`}</button>
          <ActivityFeed projectId={projectId} refreshKey={activityKey} />
        </div>
      </div>
      {sheetOpen && <TaskFilterSheet filters={filters} sortMode={sortMode} tasks={allTasks} showDone={showDone} loading={loading || completedLoading} error={error || completedError} onClose={() => setSheetOpen(false)} onApply={(next) => { updateQuery(next); setSheetOpen(false) }} />}
    </>
  )
}
