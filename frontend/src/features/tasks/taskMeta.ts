import type { TaskPriority, TaskWorkflowStatus } from '../../types/task'

export const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']

export const WORKFLOW_STATUSES: TaskWorkflowStatus[] = ['open', 'in_progress', 'done']

export function workflowLabel(status: TaskWorkflowStatus): string {
  return status === 'in_progress'
    ? 'In progress'
    : status[0].toUpperCase() + status.slice(1)
}
