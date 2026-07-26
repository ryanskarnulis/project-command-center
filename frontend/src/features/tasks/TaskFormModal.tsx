import { type FormEvent, useMemo, useState } from 'react'
import { Modal } from '../../components/Modal'
import type { Project } from '../../types/project'
import type { Task, TaskCreate, TaskPriority, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { formatDurationInput, parseDurationInput } from '../../utils/duration'
import { TaskDependencies } from './TaskDependencies'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const WORKFLOW_STATUSES: TaskWorkflowStatus[] = ['open', 'in_progress', 'done']

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

  const [title, setTitle] = useState(existingTask?.title ?? defaults?.title ?? '')
  const [description, setDescription] = useState(
    existingTask?.description ?? defaults?.description ?? ''
  )
  const [workflowStatus, setWorkflowStatus] = useState<TaskWorkflowStatus>(
    existingTask?.workflow_status ?? 'open'
  )
  const [priority, setPriority] = useState<TaskPriority>(
    existingTask?.priority ?? defaults?.priority ?? 'medium'
  )
  const [dueDate, setDueDate] = useState(
    existingTask?.due_date ?? defaults?.due_date ?? ''
  )
  // The empty ("no project") option means "inherit the parent's project" when
  // creating a subtask (create_task copies the parent's project when project_id
  // is null). A top-level task is always filed, so default it to the caller's
  // project (a project-scoped page passes its own) and fall back to General
  // rather than offering a misleading "unassigned".
  const generalProject = projects.find((p) => p.system_key === 'general')
  const isSubtaskCreate = !isEdit && defaults?.parent_task_id != null
  const [projectId, setProjectId] = useState(
    existingTask?.project_id != null ? String(existingTask.project_id)
    : defaults?.project_id != null ? String(defaults.project_id)
    : isSubtaskCreate ? ''
    : String(generalProject?.id ?? '')
  )
  const [parentId, setParentId] = useState(
    existingTask?.parent_task_id != null ? String(existingTask.parent_task_id)
    : defaults?.parent_task_id != null ? String(defaults.parent_task_id)
    : ''
  )
  // A deep-linked create modal (?new=1) can mount before the projects fetch
  // resolves, leaving projectId '' — the select then *displays* the first
  // option while submit would file to General. Adopt General during render
  // once it's known (the sanctioned derived-state reset pattern) so the
  // visible selection always matches where the task will land. Never applies
  // in edit mode ('' = unfiled) or subtask create ('' = same as parent).
  if (!isEdit && !isSubtaskCreate && projectId === '' && generalProject) {
    setProjectId(String(generalProject.id))
  }
  const [estimateDraft, setEstimateDraft] = useState(
    formatDurationInput(existingTask?.estimated_minutes ?? defaults?.estimated_minutes ?? null)
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A task with active subtasks derives its status and estimate from them; the
  // backend rejects a PATCH that carries either field (409). Disable both
  // controls and leave the fields out of the payload so the rest of the form
  // stays editable — mirrors TaskDetailView/TaskCard. (issue #191)
  const derivedFromSubtasks = existingTask?.has_subtasks === true

  const blockedParents = useMemo(
    () => (existingTask ? descendantIds(existingTask, tasks) : new Set<number>()),
    [existingTask, tasks]
  )
  const parentOptions = tasks.filter((t) => !blockedParents.has(t.id))

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setError(null)
    // Validate before flipping `saving` — an early return here used to leave
    // Save disabled forever until the modal was remounted (issue #100).
    const estimatedMinutes = parseDurationInput(estimateDraft)
    if (estimatedMinutes === undefined) {
      setError('Use something like 30m, 2h, or 1 day')
      return
    }
    setSaving(true)
    try {
      if (isEdit && existingTask) {
        await props.onSave(existingTask.id, {
          title: title.trim(),
          description: description.trim() || null,
          priority,
          due_date: dueDate || null,
          project_id: projectId === '' ? null : Number(projectId),
          parent_task_id: parentId === '' ? null : Number(parentId),
          ...(derivedFromSubtasks
            ? {}
            : {
                workflow_status: workflowStatus,
                estimated_minutes: estimatedMinutes,
              }),
        })
      } else {
        await (props as CreateMode).onSave({
          title: title.trim(),
          description: description.trim() || null,
          workflow_status: workflowStatus,
          priority,
          due_date: dueDate || null,
          // Null means "inherit the parent's project" for a subtask; the
          // backend files a top-level null into General.
          project_id: projectId === '' ? null : Number(projectId),
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
            <label htmlFor="tf-workflow-status">Status</label>
            <select
              id="tf-workflow-status"
              value={workflowStatus}
              disabled={derivedFromSubtasks}
              onChange={(e) => setWorkflowStatus(e.target.value as TaskWorkflowStatus)}
            >
              {WORKFLOW_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s === 'in_progress' ? 'in progress' : s}
                </option>
              ))}
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
          {isSubtaskCreate && <option value="">— same as parent —</option>}
          {projects.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
        </select>

        <label htmlFor="tf-parent">Parent task</label>
        <select id="tf-parent" value={parentId} onChange={(e) => setParentId(e.target.value)}>
          <option value="">— none (top level) —</option>
          {parentOptions.map((t) => <option key={t.id} value={String(t.id)}>{t.title}</option>)}
        </select>

        <label htmlFor="tf-estimate">Estimate</label>
        <input
          id="tf-estimate"
          placeholder="30m, 2h, 1 day"
          value={estimateDraft}
          disabled={derivedFromSubtasks}
          onChange={(e) => setEstimateDraft(e.target.value)}
        />
        {derivedFromSubtasks && (
          <p>Status and estimate are rolled up from this task's subtasks.</p>
        )}

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
