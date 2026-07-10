import { type DragEvent, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, FolderKanban } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useToast } from '../../components/ToastContext'
import type { ProjectOpenTasksRow } from '../../types/dashboard'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { compareTasks } from '../../utils/dates'
import { projectStatus } from '../../utils/projectStatus'
import { TaskCard } from '../tasks/TaskCard'
import { isMoveBlocked } from '../tasks/taskStatusRules'
import { useCompletedTasks } from '../tasks/useCompletedTasks'
import {
  matchesDashboardSignal,
  type DashboardSignal,
} from './dashboardSignals'

interface BoardProps {
  projects: ProjectOpenTasksRow[]
  tasks: Task[]
  signal: DashboardSignal | null
  onSetStatus: (task: Task, target: TaskWorkflowStatus) => Promise<void>
  onUpdate: (task: Task, patch: TaskUpdate) => Promise<void>
}

interface LaneProps {
  project: ProjectOpenTasksRow
  /** Every active task in the project (for the status tone). */
  activeTasks: Task[]
  /** Root tasks after the signal filter — what the columns render. */
  visibleTasks: Task[]
  signal: DashboardSignal | null
  onSetStatus: BoardProps['onSetStatus']
  onUpdate: BoardProps['onUpdate']
}

const LANE_COLUMNS = [
  ['open', 'Open'],
  ['in_progress', 'In progress'],
] as const

function DashboardSwimlane({
  project,
  activeTasks,
  visibleTasks,
  signal,
  onSetStatus,
  onUpdate,
}: LaneProps) {
  const { notify } = useToast()
  // null = no explicit choice; quiet lanes (and filtered-out lanes) start
  // collapsed, but a user toggle always wins and survives a signal change.
  const [userCollapsed, setUserCollapsed] = useState<boolean | null>(null)
  const [doneOpen, setDoneOpen] = useState(false)
  const [pendingId, setPendingId] = useState<number | null>(null)
  const [dragOverStatus, setDragOverStatus] =
    useState<TaskWorkflowStatus | null>(null)
  const completed = useCompletedTasks(project.project_id, doneOpen)
  const status = projectStatus(activeTasks, project.open_task_count)

  const quiet = signal
    ? visibleTasks.length === 0
    : project.open_task_count === 0
  const collapsed = (userCollapsed ?? quiet) && !doneOpen

  const columns = useMemo(
    () => ({
      open: visibleTasks
        .filter((task) => task.workflow_status === 'open')
        .sort(compareTasks),
      in_progress: visibleTasks
        .filter((task) => task.workflow_status === 'in_progress')
        .sort(compareTasks),
    }),
    [visibleTasks],
  )
  // Subtasks have no nesting affordance here; the archive shows roots only,
  // matching the columns.
  const completedTasks = useMemo(
    () =>
      completed.tasks
        .filter((task) => task.parent_task_id === null)
        .sort(compareTasks),
    [completed.tasks],
  )
  const byId = useMemo(() => {
    const map = new Map<number, Task>()
    for (const task of [...visibleTasks, ...completedTasks]) map.set(task.id, task)
    return map
  }, [visibleTasks, completedTasks])

  async function move(task: Task, target: TaskWorkflowStatus): Promise<void> {
    if (task.workflow_status === target) return
    // A parent's status is derived from its subtasks (read-only) — mirrors the
    // server's 409 guard.
    if (task.has_subtasks) {
      notify('error', 'Status is rolled up from subtasks')
      return
    }
    if (isMoveBlocked(task, target)) {
      notify('error', 'Blocked by an unfinished dependency')
      return
    }
    setPendingId(task.id)
    try {
      await onSetStatus(task, target)
      // Crossing the done boundary changes the archive; refresh it if open.
      if (task.workflow_status === 'done' || target === 'done') {
        completed.reload()
      }
    } finally {
      setPendingId(null)
    }
  }

  function onDrop(target: TaskWorkflowStatus, event: DragEvent): void {
    event.preventDefault()
    setDragOverStatus(null)
    const raw = event.dataTransfer.getData('text/plain')
    const task = raw ? byId.get(Number(raw)) : undefined
    if (task) void move(task, target)
  }

  function renderCard(task: Task) {
    const pending = pendingId === task.id
    return (
      <li
        key={task.id}
        className={`dashboard-lane-card${pending ? ' pending' : ''}`}
        draggable={!pending && !task.has_subtasks}
        onDragStart={(event) => {
          event.dataTransfer.setData('text/plain', String(task.id))
          event.dataTransfer.effectAllowed = 'move'
        }}
      >
        <TaskCard
          task={task}
          onComplete={() => void move(task, 'done')}
          onUpdate={(patch) => void onUpdate(task, patch)}
          onSetStatus={(target) => void move(task, target)}
        />
      </li>
    )
  }

  return (
    <section
      className={`dashboard-swimlane${quiet ? ' quiet' : ''}`}
      aria-labelledby={`dashboard-project-${project.project_id}`}
    >
      <header className="dashboard-swimlane-header">
        <button
          type="button"
          className="dashboard-lane-collapse"
          aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${project.project_name}`}
          aria-expanded={!collapsed}
          onClick={() => setUserCollapsed(!collapsed)}
        >
          {collapsed ? (
            <ChevronRight size={18} aria-hidden="true" />
          ) : (
            <ChevronDown size={18} aria-hidden="true" />
          )}
        </button>
        <span className={`dashboard-project-icon tone-${status.tone}`}>
          <FolderKanban size={18} aria-hidden="true" />
        </span>
        <div className="dashboard-project-title">
          <Link
            id={`dashboard-project-${project.project_id}`}
            to={`/projects/${project.project_id}`}
            state={{ from: 'dashboard' }}
            draggable={false}
          >
            {project.project_name}
          </Link>
          <span>
            {project.open_task_count} open{' '}
            {project.open_task_count === 1 ? 'task' : 'tasks'}
          </span>
        </div>
        <span className={`status-pill tone-${status.tone}`}>{status.label}</span>
        <button
          type="button"
          className="dashboard-done-toggle secondary-action"
          aria-expanded={doneOpen}
          onClick={() => setDoneOpen((open) => !open)}
        >
          {doneOpen
            ? `Hide done${completed.loading ? '' : ` (${completedTasks.length})`}`
            : 'Show done'}
        </button>
      </header>

      {!collapsed && (
        <div className="dashboard-swimlane-body">
          <div className="dashboard-lane-columns">
            {LANE_COLUMNS.map(([columnStatus, label]) => {
              const columnTasks = columns[columnStatus]
              return (
                <section
                  key={columnStatus}
                  className={`dashboard-lane-column${
                    dragOverStatus === columnStatus ? ' drag-over' : ''
                  }`}
                  aria-label={`${project.project_name} ${label}`}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setDragOverStatus(columnStatus)
                  }}
                  onDragLeave={() => setDragOverStatus(null)}
                  onDrop={(event) => onDrop(columnStatus, event)}
                >
                  <header>
                    <span>{label}</span>
                    <span className="count-badge">{columnTasks.length}</span>
                  </header>
                  {columnTasks.length === 0 ? (
                    <p className="dashboard-lane-empty">
                      {signal ? 'No matching tasks' : 'Nothing here'}
                    </p>
                  ) : (
                    <ul>{columnTasks.map(renderCard)}</ul>
                  )}
                </section>
              )
            })}
          </div>

          {doneOpen && (
            <section
              className="dashboard-done-archive"
              aria-label={`${project.project_name} completed tasks`}
            >
              <header>
                <strong>Done</strong>
                {!completed.loading && (
                  <span className="count-badge">{completedTasks.length}</span>
                )}
              </header>
              {completed.loading ? (
                <p className="dashboard-lane-empty">Loading completed tasks…</p>
              ) : completed.error ? (
                <p className="error" role="alert">
                  {completed.error}
                </p>
              ) : completedTasks.length === 0 ? (
                <p className="dashboard-lane-empty">No completed tasks</p>
              ) : (
                <ul>{completedTasks.map(renderCard)}</ul>
              )}
            </section>
          )}
        </div>
      )}
    </section>
  )
}

export function DashboardSwimlaneBoard({
  projects,
  tasks,
  signal,
  onSetStatus,
  onUpdate,
}: BoardProps) {
  if (projects.length === 0) {
    return (
      <div className="empty-state">
        No projects yet. <Link to="/projects?new=1">Create one</Link> to start a
        board.
      </div>
    )
  }

  return (
    <div className="dashboard-swimlane-board">
      {projects.map((project) => {
        // The status tone weighs the full project tree (matching project
        // detail); cards stay root-only because lanes have no nesting UI.
        const activeTasks = tasks.filter(
          (task) => task.project_id === project.project_id,
        )
        const visibleTasks = activeTasks
          .filter((task) => task.parent_task_id === null)
          .filter((task) => matchesDashboardSignal(task, signal))
        return (
          <DashboardSwimlane
            key={project.project_id}
            project={project}
            activeTasks={activeTasks}
            visibleTasks={visibleTasks}
            signal={signal}
            onSetStatus={onSetStatus}
            onUpdate={onUpdate}
          />
        )
      })}
    </div>
  )
}
