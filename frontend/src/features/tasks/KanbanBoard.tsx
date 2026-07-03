import { type DragEvent, useMemo, useState } from 'react'
import { useToast } from '../../components/ToastContext'
import type { Project } from '../../types/project'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { compareTasks } from '../../utils/dates'
import { TaskCard } from './TaskCard'

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

interface Props {
  // Active (not-done) tasks, already filtered by the page's filter bar.
  activeTasks: Task[]
  // The lazily-fetched completed archive, already filtered.
  completedTasks: Task[]
  projects?: Project[]
  isGlobal: boolean
  // Route a task to a target column. Source status lives on the task, so the
  // page can pick the recurrence-safe done/reopen endpoints vs a plain PATCH.
  onSetStatus: (task: Task, target: TaskWorkflowStatus) => Promise<void>
  // Inline chip edits (priority, due date, estimate) on a card.
  onUpdate: (task: Task, patch: TaskUpdate) => Promise<void>
}

// A move into In progress or Done requires every dependency finished. "Blocked"
// is derived server-side (is_blocked); the board mirrors the same rule the list
// and Today views enforce rather than letting an illegal transition reach the API.
function isMoveBlocked(task: Task, target: TaskWorkflowStatus): boolean {
  return task.is_blocked && task.workflow_status !== 'done' && target !== 'open'
}

export function KanbanBoard({
  activeTasks,
  completedTasks,
  projects,
  isGlobal,
  onSetStatus,
  onUpdate,
}: Props) {
  const { notify } = useToast()
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
    const done = [...completedTasks].sort(compareTasks)
    return { open, in_progress: inProgress, done }
  }, [activeTasks, completedTasks])

  async function move(task: Task, target: TaskWorkflowStatus) {
    if (task.workflow_status === target) return
    // A parent's status is derived from its subtasks (read-only) — move the
    // subtasks instead. Mirrors the server's 409 guard.
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
        draggable={!pending && !task.has_subtasks}
        onDragStart={(e) =>
          e.dataTransfer.setData('text/plain', String(task.id))
        }
      >
        <TaskCard
          task={task}
          projects={isGlobal ? projects : undefined}
          onComplete={() => void move(task, 'done')}
          onUpdate={(patch) => void onUpdate(task, patch)}
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
              <ul className="kanban-column-list">{tasks.map(renderCard)}</ul>
            )}
          </section>
        )
      })}
    </div>
  )
}
