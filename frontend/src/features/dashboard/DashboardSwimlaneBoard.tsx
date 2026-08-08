import { type DragEvent, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Eye, GripVertical } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useToast } from '../../components/ToastContext'
import {
  isProjectDrag,
  moveBefore,
  PROJECT_DRAG_TYPE,
} from '../projects/projectDrag'
import type { ProjectOpenTasksRow } from '../../types/dashboard'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { fireAndForget } from '../../utils/async'
import { compareTasks } from '../../utils/dates'
import { projectStatus } from '../../utils/projectStatus'
import { TaskCard } from '../tasks/TaskCard'
import { isMoveBlocked } from '../tasks/taskStatusRules'
import { isEffectiveTopLevel } from '../tasks/taskTree'
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
  /** Persist a new full project order (display order of every project id). */
  onReorder: (projectIds: number[]) => Promise<void>
  onCreateProject: () => void
}

interface LaneProps {
  project: ProjectOpenTasksRow
  /** Every active task in the project (for the status tone). */
  activeTasks: Task[]
  /** Root tasks after the signal filter — what the columns render. */
  visibleTasks: Task[]
  /** Board-wide lookup so a card dragged from another lane can be resolved. */
  boardTasksById: Map<number, Task>
  signal: DashboardSignal | null
  onSetStatus: BoardProps['onSetStatus']
  onUpdate: BoardProps['onUpdate']
  /** True while this lane is the one being drag-reordered. */
  laneDragging: boolean
  onLaneDragStart: (event: DragEvent) => void
  onLaneDragEnd: () => void
  onLaneDragOver: (event: DragEvent) => void
  onLaneDrop: (event: DragEvent) => void
}

const LANE_COLUMNS = [
  ['open', 'Open'],
  ['in_progress', 'In progress'],
] as const

function DashboardSwimlane({
  project,
  activeTasks,
  visibleTasks,
  boardTasksById,
  signal,
  onSetStatus,
  onUpdate,
  laneDragging,
  onLaneDragStart,
  onLaneDragEnd,
  onLaneDragOver,
  onLaneDrop,
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
  const status = projectStatus(activeTasks, activeTasks.length)

  // The header count describes what the columns render: root tasks. Subtasks
  // are called out separately rather than folded into "open tasks" — a header
  // larger than the visible cards reads as a wrong count.
  const openRootCount = activeTasks.filter(isEffectiveTopLevel).length
  const subtaskCount = activeTasks.length - openRootCount

  const quiet = signal ? visibleTasks.length === 0 : openRootCount === 0
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
        .filter(isEffectiveTopLevel)
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
    } catch {
      // The caller already surfaces the failure (error toast). Swallow here so
      // the fire-and-forget drag/click handlers below don't raise an unhandled
      // rejection.
    } finally {
      setPendingId(null)
    }
  }

  // A drop from another lane refiles the task into this project (and adopts
  // the column's status). One PATCH so the move is atomic and singly audited.
  async function moveAcross(
    task: Task,
    target: TaskWorkflowStatus,
  ): Promise<void> {
    // Parents aren't draggable, but guard anyway (mirrors move()) so a stray
    // drop can't send a derived-status PATCH the server would 409.
    if (task.has_subtasks) {
      notify('error', 'Status is rolled up from subtasks')
      return
    }
    if (task.workflow_status !== target && isMoveBlocked(task, target)) {
      notify('error', 'Blocked by an unfinished dependency')
      return
    }
    const patch: TaskUpdate = { project_id: project.project_id }
    if (task.workflow_status !== target) patch.workflow_status = target
    try {
      await onUpdate(task, patch)
    } catch {
      // The caller already toasts the failure; swallow so the fire-and-forget
      // cross-lane drop below doesn't raise an unhandled rejection.
    }
  }

  function onDrop(target: TaskWorkflowStatus, event: DragEvent): void {
    event.preventDefault()
    setDragOverStatus(null)
    const raw = event.dataTransfer.getData('text/plain')
    if (!raw) return
    const id = Number(raw)
    const local = byId.get(id)
    if (local) {
      void move(local, target)
      return
    }
    // Not one of ours: a card dragged over from another project's lane.
    // (Done-archive cards resolve only within their own lane, so a done task
    // can't be refiled from here — reopen or use the task panel instead.)
    const foreign = boardTasksById.get(id)
    if (foreign) void moveAcross(foreign, target)
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
          onUpdate={(patch) => fireAndForget(onUpdate(task, patch))}
          onSetStatus={(target) => void move(task, target)}
        />
      </li>
    )
  }

  return (
    <section
      className={`dashboard-swimlane${quiet ? ' quiet' : ''}${
        laneDragging ? ' dragging' : ''
      }`}
      aria-labelledby={`dashboard-project-${project.project_id}`}
      onDragOver={onLaneDragOver}
      onDrop={onLaneDrop}
    >
      {/* Name leads the row; the controls trail it. The decorative folder tile
          that used to sit in front of the name is gone — the status word
          carries the same tone it did. */}
      <header className="dashboard-swimlane-header">
        <div className="dashboard-project-title">
          <Link
            id={`dashboard-project-${project.project_id}`}
            to={`/projects/${project.project_id}`}
            draggable={false}
          >
            {project.project_name}
          </Link>
          <span>
            {openRootCount} open {openRootCount === 1 ? 'task' : 'tasks'}
            {subtaskCount > 0 &&
              ` · ${subtaskCount} ${subtaskCount === 1 ? 'subtask' : 'subtasks'}`}
          </span>
        </div>
        <span className={`status-pill tone-${status.tone}`}>{status.label}</span>
        <button
          type="button"
          className="dashboard-done-toggle"
          aria-expanded={doneOpen}
          onClick={() => setDoneOpen((open) => !open)}
        >
          <Eye size={12} aria-hidden="true" />
          {doneOpen
            ? `Hide done${completed.loading ? '' : ` (${completedTasks.length})`}`
            : 'Show done'}
        </button>
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
        <span
          className="dashboard-lane-grip"
          title="Drag to reorder projects"
          aria-label={`Reorder ${project.project_name}`}
          draggable
          onDragStart={onLaneDragStart}
          onDragEnd={onLaneDragEnd}
        >
          <GripVertical size={16} aria-hidden="true" />
        </span>
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
                    // Lane reorders bubble up to the section handler instead.
                    if (isProjectDrag(event)) return
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
  onReorder,
  onCreateProject,
}: BoardProps) {
  // Local copy so a lane drag can live-preview the new order; server data
  // (refetched after the reorder call) re-seeds it.
  const [lanes, setLanes] = useState(projects)
  // Cross-lane drops carry only a task id; resolve it against every card the
  // board can render (active roots — done-archive cards stay lane-local).
  const boardTasksById = useMemo(() => {
    const map = new Map<number, Task>()
    for (const task of tasks) {
      if (isEffectiveTopLevel(task)) map.set(task.id, task)
    }
    return map
  }, [tasks])
  const [draggedId, setDraggedId] = useState<number | null>(null)
  // Distinguishes a completed reorder drop from a cancelled drag in dragend.
  const dropCommitted = useRef(false)
  // Re-seed from server data during render (the sanctioned "derived state
  // reset" pattern) instead of an effect.
  const [seededFrom, setSeededFrom] = useState(projects)
  if (seededFrom !== projects) {
    setSeededFrom(projects)
    setLanes(projects)
  }

  function laneDragStart(projectId: number, event: DragEvent): void {
    event.dataTransfer.setData(PROJECT_DRAG_TYPE, String(projectId))
    event.dataTransfer.effectAllowed = 'move'
    dropCommitted.current = false
    setDraggedId(projectId)
  }

  function laneDragOver(projectId: number, event: DragEvent): void {
    if (draggedId === null) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
    setLanes((cur) => moveBefore(cur, (l) => l.project_id, draggedId, projectId))
  }

  function laneDrop(event: DragEvent): void {
    if (!isProjectDrag(event)) return
    event.preventDefault()
    dropCommitted.current = true
    setDraggedId(null)
    // `lanes` already holds the preview order; reset it if the save fails.
    onReorder(lanes.map((lane) => lane.project_id)).catch(() =>
      setLanes(projects),
    )
  }

  function laneDragEnd(): void {
    setDraggedId(null)
    if (!dropCommitted.current) setLanes(projects)
  }

  if (projects.length === 0) {
    return (
      <div className="empty-state">
        No projects yet.{' '}
        <button type="button" className="link-button" onClick={onCreateProject}>
          Create one
        </button>{' '}
        to start a board.
      </div>
    )
  }

  return (
    <div className="dashboard-swimlane-board">
      {lanes.map((project) => {
        // The status tone weighs the full project tree (matching project
        // detail); cards stay root-only because lanes have no nesting UI.
        const activeTasks = tasks.filter(
          (task) => task.project_id === project.project_id,
        )
        const visibleTasks = activeTasks
          .filter(isEffectiveTopLevel)
          .filter((task) => matchesDashboardSignal(task, signal))
        return (
          <DashboardSwimlane
            key={project.project_id}
            project={project}
            activeTasks={activeTasks}
            visibleTasks={visibleTasks}
            boardTasksById={boardTasksById}
            signal={signal}
            onSetStatus={onSetStatus}
            onUpdate={onUpdate}
            laneDragging={draggedId === project.project_id}
            onLaneDragStart={(event) => laneDragStart(project.project_id, event)}
            onLaneDragEnd={laneDragEnd}
            onLaneDragOver={(event) => laneDragOver(project.project_id, event)}
            onLaneDrop={laneDrop}
          />
        )
      })}
    </div>
  )
}
