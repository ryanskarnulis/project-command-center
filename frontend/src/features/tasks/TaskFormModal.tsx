import { type FormEvent, useMemo, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project } from '../../types/project'
import type { Task, TaskCreate, TaskPriority, TaskStatus, TaskUpdate } from '../../types/task'
import { DURATION_UNITS, splitDuration, toMinutes } from '../../utils/duration'
import { TaskDependencies } from './TaskDependencies'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const STATUSES: TaskStatus[] = ['candidate', 'accepted', 'rejected', 'done']

type CreateMode = {
  mode: 'create'
  defaults?: Partial<TaskCreate>
  onSave: (data: TaskCreate) => Promise<void>
}

type EditMode = {
  mode: 'edit'
  task: Task
  onSave: (id: number, data: TaskUpdate) => Promise<void>
}

type Props = (CreateMode | EditMode) & {
  tasks: Task[]
  projects: Project[]
  onClose: () => void
}

/** Ids of `task` itself plus all its descendants — invalid parent choices in edit mode. */
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

export function TaskFormModal(props: Props) {
  const { tasks, projects, onClose } = props
  const isEdit = props.mode === 'edit'
  const existingTask = isEdit ? props.task : null
  const defaults = isEdit ? undefined : props.defaults

  const initialSplit = existingTask?.estimated_minutes != null
    ? splitDuration(existingTask.estimated_minutes)
    : null

  const [title, setTitle] = useState(existingTask?.title ?? '')
  const [description, setDescription] = useState(existingTask?.description ?? '')
  const [status, setStatus] = useState<TaskStatus>(existingTask?.status ?? 'accepted')
  const [priority, setPriority] = useState<TaskPriority>(existingTask?.priority ?? 'medium')
  const [dueDate, setDueDate] = useState(existingTask?.due_date ?? '')
  const [projectId, setProjectId] = useState(
    existingTask?.project_id != null ? String(existingTask.project_id)
    : defaults?.parent_task_id != null ? ''
    : ''
  )
  const [parentId, setParentId] = useState(
    existingTask?.parent_task_id != null ? String(existingTask.parent_task_id)
    : defaults?.parent_task_id != null ? String(defaults.parent_task_id)
    : ''
  )
  const [estimateValue, setEstimateValue] = useState(initialSplit ? String(initialSplit.value) : '')
  const [estimateUnit, setEstimateUnit] = useState<'minutes' | 'hours' | 'days' | 'weeks'>(
    initialSplit?.unit ?? 'minutes'
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const blockedParents = useMemo(
    () => (existingTask ? descendantIds(existingTask, tasks) : new Set<number>()),
    [existingTask, tasks]
  )
  const parentOptions = tasks.filter((t) => !blockedParents.has(t.id))

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setSaving(true)
    setError(null)
    const estimatedMinutes = estimateValue === '' ? null : toMinutes(Number(estimateValue), estimateUnit)
    try {
      if (isEdit && existingTask) {
        await props.onSave(existingTask.id, {
          title: title.trim(),
          description: description.trim() || null,
          status,
          priority,
          due_date: dueDate || null,
          project_id: projectId === '' ? null : Number(projectId),
          parent_task_id: parentId === '' ? null : Number(parentId),
          estimated_minutes: estimatedMinutes,
        })
      } else {
        await (props as CreateMode).onSave({
          title: title.trim(),
          description: description.trim() || null,
          status,
          priority,
          due_date: dueDate || null,
          parent_task_id: parentId === '' ? null : Number(parentId),
          estimated_minutes: estimatedMinutes,
        })
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save task')
    } finally {
      setSaving(false)
    }
  }

  const titleText = isEdit ? 'Edit task' : 'Add task'

  return (
    <Modal open title={titleText} onClose={onClose}>
      <form onSubmit={handleSubmit}>
        <label htmlFor="tf-title">Title</label>
        <input id="tf-title" value={title} onChange={(e) => setTitle(e.target.value)} />

        <label htmlFor="tf-desc">Description</label>
        <textarea id="tf-desc" value={description} onChange={(e) => setDescription(e.target.value)} />

        {isEdit && (
          <>
            <label htmlFor="tf-status">Status</label>
            <select id="tf-status" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </>
        )}

        <label htmlFor="tf-priority">Priority</label>
        <select id="tf-priority" value={priority} onChange={(e) => setPriority(e.target.value as TaskPriority)}>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>

        <label htmlFor="tf-due">Due date</label>
        <input id="tf-due" type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} />

        <label htmlFor="tf-project">Project</label>
        <select id="tf-project" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
          <option value="">— unassigned —</option>
          {projects.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
        </select>

        <label htmlFor="tf-parent">Parent task</label>
        <select id="tf-parent" value={parentId} onChange={(e) => setParentId(e.target.value)}>
          <option value="">— none (top level) —</option>
          {parentOptions.map((t) => <option key={t.id} value={String(t.id)}>{t.title}</option>)}
        </select>

        <label htmlFor="tf-estimate-value">Estimate</label>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <input
            id="tf-estimate-value"
            type="number"
            min={1}
            placeholder="none"
            value={estimateValue}
            onChange={(e) => setEstimateValue(e.target.value)}
            style={{ width: '5rem' }}
          />
          <select
            id="tf-estimate-unit"
            value={estimateUnit}
            onChange={(e) => setEstimateUnit(e.target.value as typeof estimateUnit)}
            disabled={estimateValue === ''}
          >
            {DURATION_UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>

        {error && <p role="alert">{error}</p>}

        <div className="modal-actions">
          <button type="submit" disabled={saving || !title.trim()}>Save</button>
          <button type="button" onClick={onClose}>Cancel</button>
        </div>
      </form>

      {isEdit && existingTask && <TaskDependencies task={existingTask} tasks={tasks} />}
    </Modal>
  )
}
