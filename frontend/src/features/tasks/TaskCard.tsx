import type { DragEvent, MouseEvent as ReactMouseEvent, ReactNode } from 'react'
import { Check, Repeat } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge } from '../../components/Badge'
import type { Project } from '../../types/project'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { dueStatus, formatDueDate } from '../../utils/dates'
import { formatDuration } from '../../utils/duration'
import { formatRepeatInterval } from '../../utils/recurrence'
import { DueDateChip } from './chips/DueDateChip'
import { EstimateChip } from './chips/EstimateChip'
import { PriorityChip } from './chips/PriorityChip'
import { StatusChip } from './chips/StatusChip'
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
}: Props) {
  const taskLinkTo = useTaskLinkTo()
  const due = dueStatus(task.due_date)
  const projectName = projects?.find((p) => p.id === task.project_id)?.name
  const workflowLabel = task.workflow_status === 'in_progress'
    ? 'In progress'
    : task.workflow_status[0].toUpperCase() + task.workflow_status.slice(1)
  const editable = onUpdate !== undefined

  // Parents roll status up from subtasks; blocked tasks can't move to done —
  // same guards the list's old hover action and the board's move() enforce.
  const completeDisabled = task.has_subtasks || task.is_blocked
  const completeTitle = task.has_subtasks
    ? 'Status is rolled up from subtasks'
    : task.is_blocked
      ? 'Blocked by an unfinished dependency'
      : 'Mark done'

  function onDragStart(e: DragEvent<HTMLAnchorElement>) {
    // text/plain keeps the kanban column drop working; the custom type lets
    // sidebar projects accept only task drags.
    e.dataTransfer.setData(TASK_DRAG_TYPE, String(task.id))
    e.dataTransfer.setData('text/plain', String(task.id))
    e.dataTransfer.effectAllowed = 'move'
  }

  // The card is a <Link>, so a chip click would also navigate. Swallow the
  // anchor default for clicks inside a chip; submit buttons in chip editors
  // lose their native form submission to that preventDefault, so re-trigger it.
  function onBadgesClick(e: ReactMouseEvent<HTMLDivElement>) {
    if (!editable) return
    const el = e.target as HTMLElement
    if (!el.closest('.chip-wrap')) return
    e.preventDefault()
    const button = el.closest('button')
    if (button?.type === 'submit') button.form?.requestSubmit()
  }

  // Selecting text in an open chip editor must not start a card drag.
  function onBadgesDragStart(e: DragEvent<HTMLDivElement>) {
    if ((e.target as HTMLElement).closest('.chip-popover')) {
      e.preventDefault()
      e.stopPropagation()
    }
  }

  return (
    <Link
      to={taskLinkTo(task.id)}
      className="task-card"
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
        <div
          className="task-card-badges"
          onClick={onBadgesClick}
          onDragStart={onBadgesDragStart}
        >
          {editable ? (
            <StatusChip
              value={task.workflow_status}
              onChange={(status) =>
                onSetStatus
                  ? onSetStatus(status)
                  : onUpdate?.({ workflow_status: status })
              }
              disabled={task.has_subtasks}
              disabledHint="Rolled up from subtasks"
              onSkipOccurrence={
                onSkipOccurrence &&
                task.repeat_interval &&
                task.workflow_status !== 'done'
                  ? onSkipOccurrence
                  : undefined
              }
            />
          ) : (
            <span className={`status-pill workflow-${task.workflow_status}`}>
              {workflowLabel}
            </span>
          )}
          {editable ? (
            <PriorityChip
              value={task.priority}
              onChange={(priority) => onUpdate?.({ priority })}
            />
          ) : (
            <span className={`priority-pill priority-${task.priority}`}>{task.priority}</span>
          )}
          {task.is_blocking && task.workflow_status !== 'done' && (
            <Badge tone="red">{blockingLabel(task.blocked_task_count)}</Badge>
          )}
          {!task.is_blocking && task.is_blocked && task.workflow_status !== 'done' && (
            <Badge tone="neutral">Blocked</Badge>
          )}
          {task.due_date && task.workflow_status !== 'done' && (
            editable ? (
              <DueDateChip
                value={task.due_date}
                onChange={(due_date) => onUpdate?.({ due_date })}
              />
            ) : (
              <span className={`due due-${due}`}>
                Due {formatDueDate(task.due_date)}
              </span>
            )
          )}
          {task.estimated_minutes !== null && (
            editable ? (
              <EstimateChip
                value={task.estimated_minutes}
                onChange={(estimated_minutes) => onUpdate?.({ estimated_minutes })}
                disabled={task.has_subtasks}
                disabledHint="Sum of subtask estimates"
              />
            ) : (
              <Badge tone="neutral">~{formatDuration(task.estimated_minutes)}</Badge>
            )
          )}
          {task.repeat_interval && (
            <Badge tone="purple" className="repeat-badge">
              <Repeat size={12} aria-hidden="true" />
              {formatRepeatInterval(task.repeat_interval)}
              {task.next_occurrence_date && ` · next ${formatDueDate(task.next_occurrence_date)}`}
            </Badge>
          )}
          {task.review_status === 'candidate' && task.confidence !== null && (
            <Badge tone="neutral">conf {task.confidence.toFixed(2)}</Badge>
          )}
          {projectName !== undefined && (
            <span className="source-pill">{projectName}</span>
          )}
          {task.assignee_hint && (
            <span className="assignee-pill">👤 {task.assignee_hint}</span>
          )}
        </div>
      </div>
      {actions && (
        <div className="task-card-actions" onClick={(e) => e.preventDefault()}>
          {actions}
        </div>
      )}
    </Link>
  )
}
