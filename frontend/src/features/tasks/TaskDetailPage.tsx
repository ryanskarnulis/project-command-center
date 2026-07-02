import { type KeyboardEvent, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, PlayCircle, Repeat, SkipForward, Sparkles, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { breakDownTask, createUnscopedTask, deleteTask, getSubtasks, getTask, listAllTasks, reviewBreakdown, skipOccurrence, updateTask } from '../../api/tasks'
import { ApiError } from '../../api/client'
import { decideCandidate } from '../../api/inbox'
import { listProjects } from '../../api/projects'
import { Badge } from '../../components/Badge'
import { useBeforeUnload } from '../../hooks/useBeforeUnload'
import type { Project } from '../../types/project'
import type { EditScope, Task, TaskCreate, TaskPriority, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { formatDueDate } from '../../utils/dates'
import { formatDuration, formatDurationInput, parseDurationInput } from '../../utils/duration'
import { formatRepeatInterval } from '../../utils/recurrence'
import { BreakdownReview } from './BreakdownReview'
import { CandidateDecisionBar } from './CandidateDecisionBar'
import { EditScopeModal } from './EditScopeModal'
import { RecurrenceSeries } from './RecurrenceSeries'
import { RepeatIntervalInput } from './RepeatIntervalInput'
import { SubtaskComposer } from './SubtaskComposer'
import { TaskCard } from './TaskCard'
import { TaskDependencies } from './TaskDependencies'
import { useTrashCount } from '../trash/trashCountContext'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']
const WORKFLOW_STATUSES: TaskWorkflowStatus[] = ['open', 'in_progress', 'done']

// Field edits that can sensibly cascade to future occurrences of a recurring
// task; changing one of these on a series prompts the edit-scope choice. A
// due-date or workflow change is inherently per-occurrence and never prompts.
const SCOPABLE_FIELDS: (keyof TaskUpdate)[] = [
  'title',
  'description',
  'priority',
  'estimated_minutes',
  'repeat_interval',
]

function workflowLabel(status: TaskWorkflowStatus): string {
  if (status === 'in_progress') return 'In progress'
  return status[0].toUpperCase() + status.slice(1)
}

function blockingLabel(count: number): string {
  return `Blocking ${count} ${count === 1 ? 'task' : 'tasks'}`
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

interface TaskDraft {
  source: string
  title: string
  description: string
  estimate: string
  assignee: string
}

const EMPTY_TASK_DRAFT: TaskDraft = {
  source: '',
  title: '',
  description: '',
  estimate: '',
  assignee: '',
}

function makeTaskDraft(task: Task): TaskDraft {
  const description = task.description ?? ''
  const estimate = formatDurationInput(task.estimated_minutes)
  const assignee = task.assignee_hint ?? ''
  return {
    source: JSON.stringify([task.id, task.title, description, estimate, assignee]),
    title: task.title,
    description,
    estimate,
    assignee,
  }
}

export function TaskDetailPage() {
  const { taskId } = useParams<{ taskId: string }>()
  const id = Number(taskId)
  const navigate = useNavigate()
  const { refresh: refreshTrashCount } = useTrashCount()

  const [task, setTask] = useState<Task | null>(null)
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [loadedTaskId, setLoadedTaskId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [taskDraft, setTaskDraft] = useState<TaskDraft>(EMPTY_TASK_DRAFT)
  const [addingSubtask, setAddingSubtask] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [breakingDown, setBreakingDown] = useState(false)
  // Per-suggested-subtask in-flight guard so a double-click can't double-fire.
  const [decidingSubtaskId, setDecidingSubtaskId] = useState<number | null>(null)
  // A scopable edit to a recurring task is parked here until the user picks a
  // scope in EditScopeModal; choosing replays it with the chosen edit_scope.
  const [pendingScopePatch, setPendingScopePatch] = useState<TaskUpdate | null>(null)
  const [confirmingSkip, setConfirmingSkip] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([getTask(id), getSubtasks(id), listProjects(), listAllTasks()])
      .then(([t, subs, projs, tasks]) => {
        if (!active) return
        setTask(t)
        setSubtasks(subs)
        setProjects(projs)
        setAllTasks(tasks.some((candidate) => candidate.id === t.id) ? tasks : [t, ...tasks])
        setError(null)
        setLoadedTaskId(id)
      })
      .catch((e: unknown) => {
        if (!active) return
        if (e instanceof ApiError && e.status === 404) {
          navigate('/tasks', { replace: true })
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load task')
          setLoadedTaskId(id)
        }
      })
    return () => { active = false }
  }, [id, navigate])

  const loadedTaskDraft = task ? makeTaskDraft(task) : EMPTY_TASK_DRAFT
  const activeTaskDraft =
    taskDraft.source === loadedTaskDraft.source ? taskDraft : loadedTaskDraft
  const titleDraft = activeTaskDraft.title
  const descriptionDraft = activeTaskDraft.description
  const estimateDraft = activeTaskDraft.estimate
  const assigneeDraft = activeTaskDraft.assignee

  // Guard refresh/tab-close while a focused field holds an unsaved edit. In-app
  // navigation is already safe: clicking a <Link> blurs the field, which saves it.
  const dirty =
    task !== null &&
    (activeTaskDraft.title !== loadedTaskDraft.title ||
      activeTaskDraft.description !== loadedTaskDraft.description ||
      activeTaskDraft.estimate !== loadedTaskDraft.estimate ||
      activeTaskDraft.assignee !== loadedTaskDraft.assignee)
  useBeforeUnload(dirty)

  const parentOptions = useMemo(() => {
    if (!task) return []
    const blocked = descendantIds(task, allTasks)
    return allTasks.filter((candidate) => !blocked.has(candidate.id))
  }, [allTasks, task])

  async function applyPatch(data: TaskUpdate) {
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

  // Field edits on a task that belongs to a recurrence chain first ask whether to
  // apply forward; everything else (and non-recurring tasks) saves straight away.
  function savePatch(data: TaskUpdate) {
    const isScopable = Object.keys(data).some((key) =>
      SCOPABLE_FIELDS.includes(key as keyof TaskUpdate),
    )
    if (task?.recurrence_id && isScopable) {
      setPendingScopePatch(data)
      return
    }
    void applyPatch(data)
  }

  function resolveScope(scope: EditScope) {
    const patch = pendingScopePatch
    setPendingScopePatch(null)
    if (patch) void applyPatch({ ...patch, edit_scope: scope })
  }

  async function handleSkip() {
    if (!task) return
    setConfirmingSkip(false)
    setSaveState('saving')
    setSaveError(null)
    try {
      // Skip soft-deletes this occurrence and returns the next one; follow the
      // series forward so the user lands on the live task, not a deleted row.
      const next = await skipOccurrence(task.id)
      navigate(`/tasks/${next.id}`)
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to skip occurrence')
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
    if (next !== task.title) savePatch({ title: next })
  }

  function saveDescription() {
    if (!task) return
    const next = descriptionDraft.trim() || null
    if (next !== task.description) savePatch({ description: next })
  }

  function saveEstimate() {
    if (!task) return
    const next = parseDurationInput(estimateDraft)
    if (next === undefined) {
      setSaveState('error')
      setSaveError('Use something like 30m, 2h, or 1 day')
      return
    }
    if (next !== task.estimated_minutes) savePatch({ estimated_minutes: next })
  }

  function saveAssignee() {
    if (!task) return
    const next = assigneeDraft.trim() || null
    if (next !== task.assignee_hint) savePatch({ assignee_hint: next })
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.currentTarget.blur()
    }
  }

  async function handleAddSubtask(data: TaskCreate) {
    if (!task) return
    setSaveState('saving')
    setSaveError(null)
    try {
      const created = await createUnscopedTask(data)
      setSubtasks((items) => [...items, created])
      setAllTasks((items) => [...items, created])
      // The parent's estimate/status/has_subtasks are now derived — refresh it so
      // the read-only gating and rolled-up values reflect the new subtask.
      setTask(await getTask(task.id))
      setAddingSubtask(false)
      setSaveState('saved')
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to add subtask')
    }
  }

  async function handleBreakDown() {
    if (!task) return
    setBreakingDown(true)
    setSaveError(null)
    try {
      const suggested = await breakDownTask(task.id)
      // Drop any already-listed candidate (idempotent re-run returns the same
      // rows) before appending, so we never show a subtask twice.
      const suggestedIds = new Set(suggested.map((s) => s.id))
      setSubtasks((items) => [
        ...items.filter((s) => !suggestedIds.has(s.id)),
        ...suggested,
      ])
      setAllTasks((items) => [
        ...items.filter((s) => !suggestedIds.has(s.id)),
        ...suggested,
      ])
      if (suggested.length === 0) {
        setSaveState('saved')
        setSaveError('The model had no subtasks to suggest for this task.')
      }
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to break down task')
    } finally {
      setBreakingDown(false)
    }
  }

  // Approve/dismiss one suggested subtask. The breakdown's training row is
  // captured server-side once the last suggestion is decided.
  async function handleSubtaskDecision(subtaskId: number, action: 'approve' | 'dismiss') {
    if (!task) return
    setDecidingSubtaskId(subtaskId)
    setSaveError(null)
    try {
      await reviewBreakdown(task.id, [{ task_id: subtaskId, action }])
      setSubtasks((items) =>
        action === 'dismiss'
          ? items.filter((s) => s.id !== subtaskId)
          : items.map((s) =>
              s.id === subtaskId ? { ...s, review_status: 'accepted' } : s,
            ),
      )
      setSaveState('saved')
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to record decision')
    } finally {
      setDecidingSubtaskId(null)
    }
  }

  async function handleDelete() {
    if (!task) return
    setSaveState('saving')
    setSaveError(null)
    try {
      await deleteTask(task.id)
      void refreshTrashCount()
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

  if (loadedTaskId !== id) return <main><p>Loading…</p></main>
  if (error) return <main><p role="alert">{error}</p></main>
  if (!task) return null

  const projectName = projects.find((p) => p.id === task.project_id)?.name ?? 'Unassigned'
  const isCandidate = task.review_status === 'candidate' && task.inbox_item_id !== null
  // Subtasks suggested by "break this down" stay review_status=candidate until the
  // user approves/dismisses them; everything else is a real subtask.
  const suggestedSubtasks = subtasks.filter((s) => s.review_status === 'candidate')
  const reviewedSubtasks = subtasks.filter((s) => s.review_status !== 'candidate')
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
            <CandidateDecisionBar
              deciding={deciding}
              onDecide={(action) => void handleDecide(action)}
            />
          ) : (
            <>
              <button
                type="button"
                disabled={task.is_blocked && task.workflow_status !== 'done'}
                title={
                  task.is_blocked && task.workflow_status !== 'done'
                    ? 'Blocked by an unfinished dependency'
                    : undefined
                }
                onClick={() =>
                  savePatch({
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
              {task.repeat_interval && task.workflow_status !== 'done' && (
                <button type="button" onClick={() => setConfirmingSkip(true)}>
                  <SkipForward size={16} aria-hidden="true" />
                  Skip this occurrence
                </button>
              )}
              <button type="button" className="danger-action" onClick={() => void handleDelete()}>
                <Trash2 size={16} aria-hidden="true" />
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      {confirmingSkip && (
        <div className="skip-confirm" role="alertdialog" aria-label="Confirm skip">
          <p>
            Skip this occurrence — it&apos;ll move to trash and the next one will
            be created. Continue?
          </p>
          <div className="skip-confirm-actions">
            <button type="button" onClick={() => void handleSkip()}>
              Skip occurrence
            </button>
            <button type="button" onClick={() => setConfirmingSkip(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <EditScopeModal
        open={pendingScopePatch !== null}
        onChoose={resolveScope}
        onCancel={() => setPendingScopePatch(null)}
      />

      <section className="task-hero">
        <input
          className="task-title-input"
          aria-label="Task title"
          value={titleDraft}
          onChange={(e) =>
            setTaskDraft({ ...activeTaskDraft, title: e.target.value })
          }
          onBlur={saveTitle}
          onKeyDown={handleTitleKeyDown}
        />
        <div className="task-card-badges">
          <span className={`status-pill workflow-${task.workflow_status}`}>
            {workflowLabel(task.workflow_status)}
          </span>
          {task.is_blocking && task.workflow_status !== 'done' && (
            <Badge tone="red">{blockingLabel(task.blocked_task_count)}</Badge>
          )}
          {!task.is_blocking && task.is_blocked && task.workflow_status !== 'done' && (
            <Badge tone="neutral">Blocked</Badge>
          )}
          <span className={`priority-pill priority-${task.priority}`}>{task.priority}</span>
          {task.due_date && task.workflow_status !== 'done' && (
            <span className="due due-none">Due {formatDueDate(task.due_date)}</span>
          )}
          {task.estimated_minutes !== null && (
            <span className="estimate">~{formatDuration(task.estimated_minutes)}</span>
          )}
          {task.repeat_interval && (
            <span className="repeat-badge">
              <Repeat size={12} aria-hidden="true" />
              {formatRepeatInterval(task.repeat_interval)}
            </span>
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
            onChange={(e) =>
              setTaskDraft({ ...activeTaskDraft, description: e.target.value })
            }
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
              disabled={task.has_subtasks}
              onChange={(e) =>
                savePatch({ workflow_status: e.target.value as TaskWorkflowStatus })
              }
            >
              {WORKFLOW_STATUSES.map((status) => (
                <option key={status} value={status}>{workflowLabel(status)}</option>
              ))}
            </select>
            {task.has_subtasks && (
              <span className="task-field-hint">Rolled up from subtasks</span>
            )}
          </label>
          <label>
            Priority
            <select
              value={task.priority}
              onChange={(e) => savePatch({ priority: e.target.value as TaskPriority })}
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
              onChange={(e) => savePatch({ due_date: e.target.value || null })}
            />
          </label>
          <label>
            Repeat
            <RepeatIntervalInput
              value={task.repeat_interval}
              onChange={(next) => savePatch({ repeat_interval: next })}
              disabled={!task.due_date}
            />
          </label>
          <label>
            Project
            <select
              value={task.project_id === null ? '' : String(task.project_id)}
              onChange={(e) =>
                savePatch({ project_id: e.target.value === '' ? null : Number(e.target.value) })
              }
            >
              {/* An accepted task is always filed (the backend rehomes a null project
                  to General), so only a candidate can truly be left unassigned. */}
              {task.review_status === 'candidate' && <option value="">Unassigned</option>}
              {projects.map((project) => (
                <option key={project.id} value={String(project.id)}>{project.name}</option>
              ))}
            </select>
          </label>
          <label>
            Assignee
            <input
              aria-label="Assignee"
              value={assigneeDraft}
              onChange={(e) =>
                setTaskDraft({ ...activeTaskDraft, assignee: e.target.value })
              }
              onBlur={saveAssignee}
              placeholder="Unassigned"
            />
          </label>
          <label>
            Parent task
            <select
              value={task.parent_task_id === null ? '' : String(task.parent_task_id)}
              onChange={(e) =>
                savePatch({ parent_task_id: e.target.value === '' ? null : Number(e.target.value) })
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
              disabled={task.has_subtasks}
              onChange={(e) =>
                setTaskDraft({ ...activeTaskDraft, estimate: e.target.value })
              }
              onBlur={saveEstimate}
              placeholder="30m, 2h, 1 day"
            />
            {task.has_subtasks && (
              <span className="task-field-hint">Sum of subtask estimates</span>
            )}
          </label>
        </div>
      </section>

      {!isCandidate && (
      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Subtasks</h2>
          <div className="task-section-actions">
            <button
              type="button"
              onClick={() => void handleBreakDown()}
              disabled={breakingDown}
            >
              <Sparkles size={16} aria-hidden="true" />
              {breakingDown ? 'Breaking down…' : 'Break this down'}
            </button>
            <button type="button" onClick={() => setAddingSubtask(true)}>
              <PlayCircle size={16} aria-hidden="true" />
              Add subtask
            </button>
          </div>
        </div>
        <BreakdownReview
          suggestions={suggestedSubtasks}
          projects={projects}
          decidingId={decidingSubtaskId}
          onDecide={(subtaskId, action) => void handleSubtaskDecision(subtaskId, action)}
        />
        {reviewedSubtasks.length > 0 ? (
          <ul className="task-detail-list">
            {reviewedSubtasks.map((s) => (
              <li key={s.id}>
                <TaskCard task={s} projects={projects} />
              </li>
            ))}
          </ul>
        ) : (
          suggestedSubtasks.length === 0 && <p>No subtasks yet.</p>
        )}
        {addingSubtask && (
          <SubtaskComposer
            parent={task}
            onCreate={handleAddSubtask}
            onCancel={() => setAddingSubtask(false)}
          />
        )}
      </section>
      )}

      {!isCandidate && task.recurrence_id && (
        <RecurrenceSeries
          task={task}
          onStopped={(updated) => {
            setTask(updated)
            setAllTasks((items) =>
              items.map((item) => (item.id === updated.id ? updated : item)),
            )
          }}
        />
      )}

      {!isCandidate && <TaskDependencies task={task} tasks={allTasks} />}
    </main>
  )
}
