import type { ReactNode } from 'react'
import { Repeat } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge } from '../../components/Badge'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { dueStatus, formatDueDate } from '../../utils/dates'
import { formatDuration } from '../../utils/duration'
import { formatRepeatInterval } from '../../utils/recurrence'
import { useTaskLinkTo } from './panel/taskPanelContext'

interface Props {
  task: Task
  projects?: Project[]
  actions?: ReactNode
}

function blockingLabel(count: number): string {
  return `Blocking ${count} ${count === 1 ? 'task' : 'tasks'}`
}

export function TaskCard({ task, projects, actions }: Props) {
  const taskLinkTo = useTaskLinkTo()
  const due = dueStatus(task.due_date)
  const projectName = projects?.find((p) => p.id === task.project_id)?.name
  const workflowLabel = task.workflow_status === 'in_progress'
    ? 'In progress'
    : task.workflow_status[0].toUpperCase() + task.workflow_status.slice(1)

  return (
    <Link to={taskLinkTo(task.id)} className="task-card" aria-label={task.title}>
      <div className="task-card-body">
        <span className="task-card-title">{task.title}</span>
        <div className="task-card-badges">
          <span className={`status-pill workflow-${task.workflow_status}`}>
            {workflowLabel}
          </span>
          <span className={`priority-pill priority-${task.priority}`}>{task.priority}</span>
          {task.is_blocking && task.workflow_status !== 'done' && (
            <Badge tone="red">{blockingLabel(task.blocked_task_count)}</Badge>
          )}
          {!task.is_blocking && task.is_blocked && task.workflow_status !== 'done' && (
            <Badge tone="neutral">Blocked</Badge>
          )}
          {task.due_date && task.workflow_status !== 'done' && (
            <span className={`due due-${due}`}>
              Due {formatDueDate(task.due_date)}
            </span>
          )}
          {task.estimated_minutes !== null && (
            <Badge tone="neutral">~{formatDuration(task.estimated_minutes)}</Badge>
          )}
          {task.repeat_interval && (
            <Badge tone="purple" className="repeat-badge">
              <Repeat size={12} aria-hidden="true" />
              {formatRepeatInterval(task.repeat_interval)}
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
