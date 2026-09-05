import { Fragment } from 'react'
import type { DragEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { Check, Repeat } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge } from '../../components/Badge'
import type { Project } from '../../types/project'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { dueStatus, formatDueDate } from '../../utils/dates'
import { formatDuration, formatDurationShort } from '../../utils/duration'
import { formatRepeatInterval } from '../../utils/recurrence'
import { DueDateChip } from './chips/DueDateChip'
import { EstimateChip } from './chips/EstimateChip'
import { PriorityChip } from './chips/PriorityChip'
import { StatusChip } from './chips/StatusChip'
import {
  refusedStatusOptions,
  statusLockedReason,
  subtaskMoveRefusal,
} from './taskStatusRules'
import { useTaskLinkTo } from './panel/taskPanelContext'

/** dataTransfer type carrying a task id; sidebar projects accept drops of it. */
export const TASK_DRAG_TYPE = 'application/x-pcc-task'

interface Props {
  task: Task
  projects?: Project[]
  actions?: ReactNode
  /** When set, a one-click complete circle leads the card (hidden on done). */
  onComplete?: () => void
  /** When set, the metadata pills become inline editors (chips). */
  onUpdate?: (patch: TaskUpdate) => void
  /**
   * Routes status chip changes so callers can pick the recurrence-safe
   * done/reopen endpoints. Falls back to a plain onUpdate patch when absent.
   */
  onSetStatus?: (target: TaskWorkflowStatus) => void
  /**
   * When set on a recurring, not-done task, the status chip menu offers "Skip
   * occurrence…". The caller owns the confirm + skip call.
   */
  onSkipOccurrence?: () => void
  /**
   * Exceptions-only meta line, for rows that sit under a status group header.
   * The header already names the state, so the row drops the state word, drops
   * priority at or below Medium, and shortens what is left ("Blocks 2",
   * "Aug 4", "~45m") into a "·"-separated line of one to three facts.
   */
  dense?: boolean
  subtaskCount?: number
}

function blockingLabel(count: number): string {
  return `Blocking ${count} ${count === 1 ? 'task' : 'tasks'}`
}

export function TaskCard({
  task,
  projects,
  actions,
  onComplete,
  onUpdate,
  onSetStatus,
  onSkipOccurrence,
  dense = false,
  subtaskCount,
}: Props) {
  const taskLinkTo = useTaskLinkTo()
  const due = dueStatus(task.due_date)
  const projectName = projects?.find((p) => p.id === task.project_id)?.name
  const workflowLabel = task.workflow_status === 'in_progress'
    ? 'In progress'
    : task.workflow_status[0].toUpperCase() + task.workflow_status.slice(1)
  const editable = onUpdate !== undefined
  // Medium and Low say nothing a triage scan can act on; only the two that
  // raise a row above its neighbours survive the dense line.
  const showPriority =
    !dense || task.priority === 'urgent' || task.priority === 'high'

  // Parents are completed by their subtasks; blocked tasks can't move to done —
  // same guards the list's old hover action and the board's move() enforce.
  const completeRefusal =
    subtaskMoveRefusal(task, 'done') ??
    (task.is_blocked ? 'Blocked by an unfinished dependency' : null)
  const completeDisabled = completeRefusal !== null
  const completeTitle = completeRefusal ?? 'Mark done'
  // A parent whose subtasks are all done has no status move left; otherwise the
  // chip stays live and only the refused targets are switched off.
  const statusLock = statusLockedReason(task)

  function onDragStart(e: DragEvent<HTMLAnchorElement>) {
    // text/plain keeps the kanban column drop working; the custom type lets
    // sidebar projects accept only task drags.
    e.dataTransfer.setData(TASK_DRAG_TYPE, String(task.id))
    e.dataTransfer.setData('text/plain', String(task.id))
    e.dataTransfer.effectAllowed = 'move'
  }

  // The card is a <Link>, so opening a chip would also navigate. Swallow the
  // anchor default for clicks on a chip trigger. Clicks *inside* an open chip
  // popover never get here — it is portaled to <body> and stops them at its
  // own boundary — which leaves native form submission in the chip editors
  // (Estimate, Repeat) intact.
  function onBadgesClick(e: ReactMouseEvent<HTMLDivElement>) {
    if (!editable) return
    if (!(e.target as HTMLElement).closest('.chip-wrap')) return
    e.preventDefault()
  }

  // Selecting text in an open chip editor must not start a card drag.
  function onBadgesDragStart(e: DragEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest('.chip-popover')) {
      e.preventDefault()
      e.stopPropagation()
    }
  }

  // Built as a list so the dense form can put a "·" between the facts that
  // actually rendered. The separators are siblings, never pseudo-content inside
  // a chip <button> — there a screen reader would read them as part of the
  // control's name.
  const meta: ReactNode[] = []
  if (!dense) {
    meta.push(
      editable ? (
        <StatusChip
          value={task.workflow_status}
          onChange={(status) =>
            onSetStatus ? onSetStatus(status) : onUpdate?.({ workflow_status: status })
          }
          disabled={statusLock !== null}
          disabledHint={statusLock ?? undefined}
          disabledOptions={refusedStatusOptions(task)}
          onSkipOccurrence={
            onSkipOccurrence && task.repeat_interval && task.workflow_status !== 'done'
              ? onSkipOccurrence
              : undefined
          }
        />
      ) : (
        <span className={`status-pill workflow-${task.workflow_status}`}>
          {workflowLabel}
        </span>
      )
    )
  }
  if (showPriority) {
    meta.push(
      editable ? (
        <PriorityChip
          value={task.priority}
          onChange={(priority) => onUpdate?.({ priority })}
        />
      ) : (
        <span className={`priority-pill priority-${task.priority}`}>{task.priority}</span>
      )
    )
  }
  if (task.is_blocking && task.workflow_status !== 'done') {
    meta.push(
      <Badge tone="red">
        {dense ? `Blocks ${task.blocked_task_count}` : blockingLabel(task.blocked_task_count)}
      </Badge>
    )
  }
  // "Blocked" survives the dense line: it is the one state word no group header
  // above the row ever says.
  if (!task.is_blocking && task.is_blocked && task.workflow_status !== 'done') {
    meta.push(<Badge tone="neutral">Blocked</Badge>)
  }
  if (task.due_date && task.workflow_status !== 'done') {
    meta.push(
      editable ? (
        <DueDateChip
          value={task.due_date}
          onChange={(due_date) => onUpdate?.({ due_date })}
          dense={dense}
        />
      ) : (
        <span className={`due due-${due}`}>
          {dense ? formatDueDate(task.due_date) : `Due ${formatDueDate(task.due_date)}`}
        </span>
      )
    )
  }
  if (task.estimated_minutes !== null) {
    meta.push(
      editable ? (
        <EstimateChip
          value={task.estimated_minutes}
          onChange={(estimated_minutes) => onUpdate?.({ estimated_minutes })}
          disabled={task.has_subtasks}
          disabledHint="Sum of subtask estimates"
          dense={dense}
        />
      ) : (
        <Badge tone="neutral">
          ~{dense ? formatDurationShort(task.estimated_minutes) : formatDuration(task.estimated_minutes)}
        </Badge>
      )
    )
  }
  if (task.repeat_interval) {
    meta.push(
      <Badge tone="purple" className="repeat-badge">
        <Repeat size={12} aria-hidden="true" />
        {formatRepeatInterval(task.repeat_interval)}
        {task.next_occurrence_date && ` · next ${formatDueDate(task.next_occurrence_date)}`}
      </Badge>
    )
  }
  if (projectName !== undefined) {
    meta.push(<span className="source-pill">{projectName}</span>)
  }
  if (subtaskCount) {
    meta.push(<span>{subtaskCount} {subtaskCount === 1 ? 'subtask' : 'subtasks'}</span>)
  }

  return (
    <Link
      to={taskLinkTo(task.id)}
      className={`task-card workflow-${task.workflow_status}${dense ? ' dense' : ''}`}
      aria-label={task.title}
      draggable
      onDragStart={onDragStart}
    >
      {onComplete && task.workflow_status !== 'done' && (
        <button
          type="button"
          className="task-complete-circle"
          aria-label={`Mark ${task.title} done`}
          title={completeTitle}
          disabled={completeDisabled}
          onClick={(e) => {
            e.preventDefault()
            onComplete()
          }}
        >
          <Check size={13} aria-hidden="true" />
        </button>
      )}
      <div className="task-card-body">
        <span className="task-card-title">{task.title}</span>
        {/* A dense row with nothing exceptional to say prints no meta line at
            all — an empty one would still spend the body's row gap. */}
        {meta.length > 0 && (
          <div
            className="task-card-badges"
            onClick={onBadgesClick}
            onDragStart={onBadgesDragStart}
          >
            {meta.map((item, index) => (
              <Fragment key={index}>
                {dense && index > 0 && (
                  <span className="meta-sep" aria-hidden="true">
                    ·
                  </span>
                )}
                {item}
              </Fragment>
            ))}
          </div>
        )}
      </div>
      {actions && (
        <div className="task-card-actions" onClick={(e) => e.preventDefault()}>
          {actions}
        </div>
      )}
    </Link>
  )
}
