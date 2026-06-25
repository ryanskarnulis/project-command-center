import { type FormEvent, useMemo, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { formatDurationInput, parseDurationInput } from '../../utils/duration'
import { TaskDependencies } from './TaskDependencies'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const WORKFLOW_STATUSES: TaskWorkflowStatus[] = ['open', 'in_progress', 'done']

interface Props {
  task: Task
  tasks: Task[]
  projects: Project[]
  onClose: () => void
  onSave: (id: number, data: TaskUpdate) => Promise<void>
}

/** Ids of `task` itself plus all its descendants — invalid parent choices. */
function descendantIds(task: Task, tasks: Task[]): Set<number> {
  const childrenOf = new Map<number, Task[]>()
  for (const t of tasks) {
    if (t.parent_task_id !== null) {
      const group = childrenOf.get(t.parent_task_id) ?? []
      group.push(t)
      childrenOf.set(t.parent_task_id, group)
    }
  }
  const blocked = new Set<number>([task.id])
  const stack = [task.id]
  while (stack.length > 0) {
    const id = stack.pop() as number
    for (const child of childrenOf.get(id) ?? []) {
      if (!blocked.has(child.id)) {
        blocked.add(child.id)
        stack.push(child.id)
      }
    }
  }
  return blocked
}

export function TaskEditModal({ task, tasks, projects, onClose, onSave }: Props) {
  const [title, setTitle] = useState(task.title)
  const [description, setDescription] = useState(task.description ?? '')
  const [workflowStatus, setWorkflowStatus] = useState<TaskWorkflowStatus>(task.workflow_status)
  const [priority, setPriority] = useState<TaskPriority>(task.priority)
  const [dueDate, setDueDate] = useState(task.due_date ?? '')
  const [projectId, setProjectId] = useState(task.project_id !== null ? String(task.project_id) : '')
  const [parentId, setParentId] = useState(task.parent_task_id !== null ? String(task.parent_task_id) : '')
  const [estimateDraft, setEstimateDraft] = useState(formatDurationInput(task.estimated_minutes))
  const [assignee, setAssignee] = useState(task.assignee_hint ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A task can't be parented to itself or any of its own descendants (cycle).
  const blocked = useMemo(() => descendantIds(task, tasks), [task, tasks])
  const parentOptions = tasks.filter((t) => !blocked.has(t.id))

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setSaving(true)
    setError(null)
    const estimatedMinutes = parseDurationInput(estimateDraft)
    if (estimatedMinutes === undefined) {
      setError('Use something like 30m, 2h, or 1 day')
      setSaving(false)
      return
    }
    try {
      await onSave(task.id, {
        title: title.trim(),
        description: description.trim() || null,
        workflow_status: workflowStatus,
        priority,
        due_date: dueDate || null,
        project_id: projectId === '' ? null : Number(projectId),
        parent_task_id: parentId === '' ? null : Number(parentId),
        estimated_minutes: estimatedMinutes,
        assignee_hint: assignee.trim() || null,
      })
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save task')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open title="Edit task" onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label htmlFor="te-title">Title</label>
        <input
          id="te-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        <label htmlFor="te-desc">Description</label>
        <textarea
          id="te-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        <label htmlFor="te-status">Status</label>
        <select
          id="te-status"
          value={workflowStatus}
          onChange={(e) => setWorkflowStatus(e.target.value as TaskWorkflowStatus)}
        >
          {WORKFLOW_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s === 'in_progress' ? 'in progress' : s}
            </option>
          ))}
        </select>

        <label htmlFor="te-priority">Priority</label>
        <select
          id="te-priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        <label htmlFor="te-due">Due date</label>
        <input
          id="te-due"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />

        <label htmlFor="te-project">Project</label>
        <select
          id="te-project"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
        >
          {/* An accepted task is always filed (the backend rehomes a null project to
              General), so only a candidate can truly be left unassigned. */}
          {task.review_status === 'candidate' && <option value="">— unassigned —</option>}
          {projects.map((p) => (
            <option key={p.id} value={String(p.id)}>
              {p.name}
            </option>
          ))}
        </select>

        <label htmlFor="te-assignee">Assignee</label>
        <input
          id="te-assignee"
          placeholder="Unassigned"
          value={assignee}
          onChange={(e) => setAssignee(e.target.value)}
        />

        <label htmlFor="te-parent">Parent task</label>
        <select
          id="te-parent"
          value={parentId}
          onChange={(e) => setParentId(e.target.value)}
        >
          <option value="">— none (top level) —</option>
          {parentOptions.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.title}
            </option>
          ))}
        </select>

        <label htmlFor="te-estimate">Estimate</label>
        <input
          id="te-estimate"
          placeholder="30m, 2h, 1 day"
          value={estimateDraft}
          onChange={(e) => setEstimateDraft(e.target.value)}
        />

        {error && <p role="alert">{error}</p>}

        <div className="modal-actions">
          <button type="submit" disabled={saving || !title.trim()}>
            Save
          </button>
          <button type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>

      <TaskDependencies task={task} tasks={tasks} />
    </Modal>
  )
}
