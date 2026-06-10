import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, PlayCircle, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { createUnscopedTask, deleteTask, getSubtasks, getTask, listAllTasks, updateTask } from '../../api/tasks'
import { decideCandidate } from '../../api/inbox'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { formatDueDate } from '../../utils/dates'
import { formatDuration, formatDurationInput, parseDurationInput } from '../../utils/duration'
import { TaskCard } from './TaskCard'
import { TaskDependencies } from './TaskDependencies'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const WORKFLOW_STATUSES: TaskWorkflowStatus[] = ['open', 'in_progress', 'done']

function workflowLabel(status: TaskWorkflowStatus): string {
  if (status === 'in_progress') return 'In progress'
  return status[0].toUpperCase() + status.slice(1)
}

/** Ids of `task` itself plus all descendants — invalid parent choices. */
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

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const id = Number(taskId)
  const navigate = useNavigate()

  const [task, setTask] = useState<Task | null>(null)
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')
  const [descriptionDraft, setDescriptionDraft] = useState('')
  const [estimateDraft, setEstimateDraft] = useState('')
  const EMPTY_SUBTASK_DRAFT = {
    title: '',
    priority: 'medium' as TaskPriority,
    dueDate: '',
    estimate: '',
  }
  const [subtaskDraft, setSubtaskDraft] = useState(EMPTY_SUBTASK_DRAFT)
  const [subtaskError, setSubtaskError] = useState<string | null>(null)
  const [addingSubtask, setAddingSubtask] = useState(false)
  const [deciding, setDeciding] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([getTask(id), getSubtasks(id), listProjects(), listAllTasks()])
      .then(([t, subs, projs, tasks]) => {
        if (!active) return
        setTask(t)
        setSubtasks(subs)
        setProjects(projs)
        setAllTasks(tasks.some((candidate) => candidate.id === t.id) ? tasks : [t, ...tasks])
        setError(null)
      })
      .catch((e: unknown) => {
        if (!active) return
        const msg = e instanceof Error ? e.message : 'Failed to load task'
        if (msg.includes('404') || msg.includes('not found')) {
          navigate('/tasks', { replace: true })
        } else {
          setError(msg)
        }
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, navigate])

  useEffect(() => {
    if (!task) return
    setTitleDraft(task.title)
    setDescriptionDraft(task.description ?? '')
    setEstimateDraft(formatDurationInput(task.estimated_minutes))
  }, [task])

  const parentOptions = useMemo(() => {
    if (!task) return []
    const blocked = descendantIds(task, allTasks)
    return allTasks.filter((candidate) => !blocked.has(candidate.id))
  }, [allTasks, task])

  async function savePatch(data: TaskUpdate) {
    if (!task) return
    setSaveState('saving')
    setSaveError(null)
    try {
      const updated = await updateTask(task.id, data)
      setTask(updated)
      setAllTasks((items) => items.map((item) => item.id === updated.id ? updated : item))
      setSaveState('saved')
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to save task')
    }
  }

  function saveTitle() {
    if (!task) return
    const next = titleDraft.trim()
    if (!next) {
      setSaveState('error')
      setSaveError('Title is required')
      return
    }
    if (next !== task.title) void savePatch({ title: next })
  }

  function saveDescription() {
    if (!task) return
    const next = descriptionDraft.trim() || null
    if (next !== task.description) void savePatch({ description: next })
  }

  function saveEstimate() {
    if (!task) return
    const next = parseDurationInput(estimateDraft)
    if (next === undefined) {
      setSaveState('error')
      setSaveError('Use something like 30m, 2h, or 1 day')
      return
    }
    if (next !== task.estimated_minutes) void savePatch({ estimated_minutes: next })
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.currentTarget.blur()
    }
  }

  function closeSubtaskComposer() {
    setAddingSubtask(false)
    setSubtaskError(null)
  }

  async function handleAddSubtask(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!task || !subtaskDraft.title.trim()) return
    const estimatedMinutes = parseDurationInput(subtaskDraft.estimate)
    if (estimatedMinutes === undefined) {
      setSubtaskError('Use something like 30m, 2h, or 1 day')
      return
    }
    setSaveState('saving')
    setSaveError(null)
    try {
      const created = await createUnscopedTask({
        title: subtaskDraft.title.trim(),
        parent_task_id: task.id,
        priority: subtaskDraft.priority,
        due_date: subtaskDraft.dueDate || null,
        estimated_minutes: estimatedMinutes,
      })
      setSubtasks((items) => [...items, created])
      setAllTasks((items) => [...items, created])
      setSubtaskDraft(EMPTY_SUBTASK_DRAFT)
      setSubtaskError(null)
      setAddingSubtask(false)
      setSaveState('saved')
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to add subtask')
    }
  }

  async function handleDelete() {
    if (!task) return
    setSaveState('saving')
    setSaveError(null)
    try {
      await deleteTask(task.id)
      navigate('/tasks')
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to delete task')
    }
  }

  // Approve/dismiss a candidate. Inline field edits already persisted via
  // savePatch, so this only flips review_status (and finalizes the note when it
  // was the last candidate). Send the project explicitly only when one is set, so
  // the backend's suggested-project fallback still applies to untouched candidates.
  async function handleDecide(action: 'approve' | 'dismiss') {
    if (!task || task.inbox_item_id === null) return
    const inboxItemId = task.inbox_item_id
    setDeciding(true)
    setSaveError(null)
    try {
      const edits =
        action === 'approve' && task.project_id !== null
          ? { project_id: task.project_id }
          : undefined
      const res = await decideCandidate(inboxItemId, task.id, { action, edits })
      navigate(res.finalized ? '/inbox' : `/inbox/${inboxItemId}`)
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to record decision')
      setDeciding(false)
    }
  }

  if (loading) return <main><p>Loading…</p></main>
  if (error) return <main><p role="alert">{error}</p></main>
  if (!task) return null

  const projectName = projects.find((p) => p.id === task.project_id)?.name ?? 'Unassigned'
  const isCandidate = task.review_status === 'candidate' && task.inbox_item_id !== null
  const saveLabel = saveState === 'saving'
    ? 'Saving…'
    : saveState === 'saved'
      ? 'Saved'
      : saveState === 'error'
        ? 'Could not save'
        : ''

  return (
    <main className="task-detail">
      <div className="task-detail-header">
        {isCandidate ? (
          <p className="breadcrumb">
            <Link to="/inbox">Inbox</Link>
            {' › '}
            <Link to={`/inbox/${task.inbox_item_id}`}>Note review</Link>
            {' › '}
            <span aria-current="page">{task.title}</span>
          </p>
        ) : (
          <p><Link to="/tasks">← Open Tasks</Link></p>
        )}
        <div className="task-detail-actions">
          {saveLabel && (
            <span
              className={saveState === 'error' ? 'save-state error' : 'save-state'}
              role={saveState === 'error' ? 'alert' : 'status'}
            >
              {saveLabel}
            </span>
          )}
          {isCandidate ? (
            <>
              <button
                type="button"
                disabled={deciding}
                onClick={() => void handleDecide('approve')}
              >
                <CheckCircle2 size={16} aria-hidden="true" />
                Approve
              </button>
              <button
                type="button"
                className="danger-action"
                disabled={deciding}
                onClick={() => void handleDecide('dismiss')}
              >
                <Trash2 size={16} aria-hidden="true" />
                Dismiss
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() =>
                  void savePatch({
                    workflow_status: task.workflow_status === 'done' ? 'open' : 'done',
                  })
                }
              >
                {task.workflow_status === 'done' ? (
                  <Circle size={16} aria-hidden="true" />
                ) : (
                  <CheckCircle2 size={16} aria-hidden="true" />
                )}
                {task.workflow_status === 'done' ? 'Reopen' : 'Mark done'}
              </button>
              <button type="button" className="danger-action" onClick={() => void handleDelete()}>
                <Trash2 size={16} aria-hidden="true" />
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      <section className="task-hero">
        <input
          className="task-title-input"
          aria-label="Task title"
          value={titleDraft}
          onChange={(e) => setTitleDraft(e.target.value)}
          onBlur={saveTitle}
          onKeyDown={handleTitleKeyDown}
        />
        <div className="task-card-badges">
          <span className={`status-pill workflow-${task.workflow_status}`}>
            {workflowLabel(task.workflow_status)}
          </span>
          {task.is_blocked && task.workflow_status !== 'done' && (
            <span className="blocked">Blocked</span>
          )}
          <span className={`priority-pill priority-${task.priority}`}>{task.priority}</span>
          {task.due_date && task.workflow_status !== 'done' && (
            <span className="due due-none">Due {formatDueDate(task.due_date)}</span>
          )}
          {task.estimated_minutes !== null && (
            <span className="estimate">~{formatDuration(task.estimated_minutes)}</span>
          )}
          <span className="source-pill">{projectName}</span>
        </div>
        {saveError && <p role="alert" className="error">{saveError}</p>}
      </section>

      <section className="task-detail-grid">
        <div className="task-detail-panel task-description-panel">
          <div className="task-section-heading">
            <h2>Description</h2>
          </div>
          <textarea
            aria-label="Task description"
            value={descriptionDraft}
            onChange={(e) => setDescriptionDraft(e.target.value)}
            onBlur={saveDescription}
            placeholder="Add a description"
            rows={5}
          />
        </div>

        <div className="task-detail-panel task-fields-panel">
          <div className="task-section-heading">
            <h2>Task Fields</h2>
          </div>
          <label>
            Status
            <select
              value={task.workflow_status}
              onChange={(e) =>
                void savePatch({ workflow_status: e.target.value as TaskWorkflowStatus })
              }
            >
              {WORKFLOW_STATUSES.map((status) => (
                <option key={status} value={status}>{workflowLabel(status)}</option>
              ))}
            </select>
          </label>
          <label>
            Priority
            <select
              value={task.priority}
              onChange={(e) => void savePatch({ priority: e.target.value as TaskPriority })}
            >
              {PRIORITIES.map((priority) => (
                <option key={priority} value={priority}>{priority}</option>
              ))}
            </select>
          </label>
          <label>
            Due date
            <input
              type="date"
              value={task.due_date ?? ''}
              onChange={(e) => void savePatch({ due_date: e.target.value || null })}
            />
          </label>
          <label>
            Project
            <select
              value={task.project_id === null ? '' : String(task.project_id)}
              onChange={(e) =>
                void savePatch({ project_id: e.target.value === '' ? null : Number(e.target.value) })
              }
            >
              <option value="">Unassigned</option>
              {projects.map((project) => (
                <option key={project.id} value={String(project.id)}>{project.name}</option>
              ))}
            </select>
          </label>
          <label>
            Parent task
            <select
              value={task.parent_task_id === null ? '' : String(task.parent_task_id)}
              onChange={(e) =>
                void savePatch({ parent_task_id: e.target.value === '' ? null : Number(e.target.value) })
              }
            >
              <option value="">None</option>
              {parentOptions.map((option) => (
                <option key={option.id} value={String(option.id)}>{option.title}</option>
              ))}
            </select>
          </label>
          <label>
            Estimate
            <input
              aria-label="Estimate"
              value={estimateDraft}
              onChange={(e) => setEstimateDraft(e.target.value)}
              onBlur={saveEstimate}
              placeholder="30m, 2h, 1 day"
            />
          </label>
        </div>
      </section>

      {!isCandidate && (
      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Subtasks</h2>
          <button type="button" onClick={() => setAddingSubtask(true)}>
            <PlayCircle size={16} aria-hidden="true" />
            Add subtask
          </button>
        </div>
        {subtasks.length > 0 ? (
          <ul className="task-detail-list">
            {subtasks.map((s) => (
              <li key={s.id}>
                <TaskCard task={s} projects={projects} />
              </li>
            ))}
          </ul>
        ) : (
          <p>No subtasks yet.</p>
        )}
        {addingSubtask && (
          <form className="task-subtask-form" onSubmit={(e) => void handleAddSubtask(e)}>
            <input
              autoFocus
              aria-label="Subtask title"
              value={subtaskDraft.title}
              onChange={(e) =>
                setSubtaskDraft((d) => ({ ...d, title: e.target.value }))
              }
              placeholder="Subtask title"
            />
            <div className="task-subtask-fields">
              <label>
                <span>Priority</span>
                <select
                  value={subtaskDraft.priority}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({
                      ...d,
                      priority: e.target.value as TaskPriority,
                    }))
                  }
                >
                  <option value="urgent">Urgent</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </label>
              <label>
                <span>Due date</span>
                <input
                  type="date"
                  value={subtaskDraft.dueDate}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({ ...d, dueDate: e.target.value }))
                  }
                />
              </label>
              <label>
                <span>Estimate</span>
                <input
                  placeholder="30m, 2h, 1 day"
                  value={subtaskDraft.estimate}
                  onChange={(e) =>
                    setSubtaskDraft((d) => ({ ...d, estimate: e.target.value }))
                  }
                />
              </label>
            </div>
            {subtaskError && <p role="alert">{subtaskError}</p>}
            <div className="task-subtask-actions">
              <button type="submit" disabled={!subtaskDraft.title.trim()}>Add</button>
              <button type="button" onClick={closeSubtaskComposer}>Cancel</button>
            </div>
          </form>
        )}
      </section>
      )}

      {!isCandidate && <TaskDependencies task={task} tasks={allTasks} />}
    </main>
  )
}
