import { type DragEvent, useMemo, useState } from 'react'
import { useToast } from '../../components/ToastContext'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { fireAndForget } from '../../utils/async'
import { compareTasks } from '../../utils/dates'
import { TaskCard } from './TaskCard'
import {
  isMoveBlocked,
  statusLockedReason,
  subtaskMoveRefusal,
} from './taskStatusRules'

interface Column {
  status: TaskWorkflowStatus
  label: string
}

// Done is sourced from the completed archive, the other two from the active list.
const COLUMNS: Column[] = [
  { status: 'open', label: 'Open' },
  { status: 'in_progress', label: 'In progress' },
  { status: 'done', label: 'Done' },
]

// The archive grows without bound while Open/In progress stay short, so the
// Done column shows only the most recently finished few until asked for more.
const DONE_PREVIEW = 5

interface Props {
  // Active (not-done) tasks, already filtered by the page's filter bar.
  activeTasks: Task[]
  // The lazily-fetched completed archive, already filtered.
  completedTasks: Task[]
  // Route a task to a target column. Source status lives on the task, so the
  // page can pick the recurrence-safe done/reopen endpoints vs a plain PATCH.
  onSetStatus: (task: Task, target: TaskWorkflowStatus) => Promise<void>
  // Inline chip edits (priority, due date, estimate) on a card.
  onUpdate: (task: Task, patch: TaskUpdate) => Promise<void>
  // Live subtask count per root, shown on the card's meta line.
  subtaskCounts?: Map<number, number>
}

export function KanbanBoard({
  activeTasks,
  completedTasks,
  onSetStatus,
  onUpdate,
  subtaskCounts,
}: Props) {
  const { notify } = useToast()
  const [showAllDone, setShowAllDone] = useState(false)
  const [pendingId, setPendingId] = useState<number | null>(null)
  const [dragOverStatus, setDragOverStatus] = useState<TaskWorkflowStatus | null>(
    null,
  )

  // One lookup table across both sources so a drop can resolve the dragged id.
  const byId = useMemo(() => {
    const map = new Map<number, Task>()
    for (const t of [...activeTasks, ...completedTasks]) map.set(t.id, t)
    return map
  }, [activeTasks, completedTasks])

  const columnTasks = useMemo(() => {
    const open = activeTasks
      .filter((t) => t.workflow_status === 'open')
      .sort(compareTasks)
    const inProgress = activeTasks
      .filter((t) => t.workflow_status === 'in_progress')
      .sort(compareTasks)
    // Most recently finished first: completing a task is its last write.
    const done = [...completedTasks].sort(
      (a, b) => b.updated_at.localeCompare(a.updated_at) || b.id - a.id,
    )
    return { open, in_progress: inProgress, done }
  }, [activeTasks, completedTasks])

  async function move(task: Task, target: TaskWorkflowStatus) {
    if (task.workflow_status === target) return
    // A parent is completed by its subtasks, and reopened only while they leave
    // the question open — mirrors the server's 409 guard.
    const refusal = subtaskMoveRefusal(task, target)
    if (refusal) {
      notify('error', refusal)
      return
    }
    if (isMoveBlocked(task, target)) {
      notify('error', 'Blocked by an unfinished dependency')
      return
    }
    setPendingId(task.id)
    try {
      await onSetStatus(task, target)
    } catch {
      // The caller already surfaces the failure (toast or board error state).
      // Swallow here so the fire-and-forget drag/click handlers below don't
      // raise an unhandled rejection.
    } finally {
      setPendingId(null)
    }
  }

  function onDrop(target: TaskWorkflowStatus, e: DragEvent) {
    e.preventDefault()
    setDragOverStatus(null)
    const raw = e.dataTransfer.getData('text/plain')
    const task = raw ? byId.get(Number(raw)) : undefined
    if (task) void move(task, target)
  }

  function renderCard(task: Task) {
    const pending = pendingId === task.id
    return (
      <li
        key={task.id}
        className="kanban-card"
        draggable={!pending && statusLockedReason(task) === null}
        onDragStart={(e) =>
          e.dataTransfer.setData('text/plain', String(task.id))
        }
      >
        {/* The live columns are dense — the header already names the status —
            as on the dashboard lanes. Done keeps the full line: its status
            chip is the only non-drag way back to In progress (#148). */}
        <TaskCard
          task={task}
          dense={task.workflow_status !== 'done'}
          subtaskCount={subtaskCounts?.get(task.id)}
          onComplete={() => void move(task, 'done')}
          onUpdate={(patch) => fireAndForget(onUpdate(task, patch))}
          onSetStatus={(target) => void move(task, target)}
        />
      </li>
    )
  }

  return (
    <div className="kanban-board">
      {COLUMNS.map((col) => {
        const tasks = columnTasks[col.status]
        return (
          <section
            key={col.status}
            className={`kanban-column${
              dragOverStatus === col.status ? ' drag-over' : ''
            }`}
            aria-label={col.label}
            onDragOver={(e) => {
              e.preventDefault()
              setDragOverStatus(col.status)
            }}
            onDragLeave={() => setDragOverStatus(null)}
            onDrop={(e) => onDrop(col.status, e)}
          >
            <header className="kanban-column-header">
              <span>{col.label}</span>
              <span className="count-badge">{tasks.length}</span>
            </header>
            {tasks.length === 0 ? (
              <p className="kanban-empty">Nothing here</p>
            ) : (
              <ul className="kanban-column-list">
                {(col.status === 'done' && !showAllDone
                  ? tasks.slice(0, DONE_PREVIEW)
                  : tasks
                ).map(renderCard)}
              </ul>
            )}
            {col.status === 'done' && tasks.length > DONE_PREVIEW && (
              <button
                type="button"
                className="kanban-show-all"
                aria-expanded={showAllDone}
                onClick={() => setShowAllDone((v) => !v)}
              >
                {showAllDone ? 'Show fewer' : `Show all ${tasks.length}`}
              </button>
            )}
          </section>
        )
      })}
    </div>
  )
}
