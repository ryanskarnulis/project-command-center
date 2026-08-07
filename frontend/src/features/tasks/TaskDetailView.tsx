import { type KeyboardEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { CheckCircle2, Circle, PlayCircle, SkipForward, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { createUnscopedTask, deleteTask, getSubtasks, getTask, listAllTasks, skipOccurrence } from '../../api/tasks'
import { ApiError } from '../../api/client'
import { listProjects } from '../../api/projects'
import { Badge } from '../../components/Badge'
import { useBeforeUnload } from '../../hooks/useBeforeUnload'
import { useFieldDraft } from '../../hooks/useFieldDraft'
import type { Project } from '../../types/project'
import type { Task, TaskCreate } from '../../types/task'
import { formatDueDate } from '../../utils/dates'
import { EditScopeModal } from './EditScopeModal'
import { RecurrenceSeries } from './RecurrenceSeries'
import { SubtaskComposer } from './SubtaskComposer'
import { TaskCard } from './TaskCard'
import { TaskDependencies } from './TaskDependencies'
import { useTaskPanel } from './panel/taskPanelContext'
import { useScopedTaskUpdate } from './useScopedTaskUpdate'
import { useTrashCount } from '../trash/trashCountContext'
import { DueDateChip } from './chips/DueDateChip'
import { EstimateChip } from './chips/EstimateChip'
import { ParentTaskChip } from './chips/ParentTaskChip'
import { PriorityChip } from './chips/PriorityChip'
import { ProjectChip } from './chips/ProjectChip'
import { RepeatChip } from './chips/RepeatChip'
import { StatusChip } from './chips/StatusChip'

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

interface Props {
  taskId: number
  /** Close the hosting panel; when absent (standalone page) falls back to navigation. */
  onClose?: () => void
  /** Host refresh after any successful mutation, so the list behind stays current. */
  onMutated?: () => void
}

/**
 * The task detail surface — hero with editable metadata chips, description,
 * subtasks, recurrence series, and dependencies. Prop-driven so it can render
 * inside the slide-over peek panel or as a standalone route.
 */
export function TaskDetailView({ taskId: id, onClose, onMutated }: Props) {
  const navigate = useNavigate()
  const panel = useTaskPanel()
  const { refresh: refreshTrashCount } = useTrashCount()

  const [task, setTask] = useState<Task | null>(null)
  const [subtasks, setSubtasks] = useState<Task[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [allTasks, setAllTasks] = useState<Task[]>([])
  const [loadedTaskId, setLoadedTaskId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [addingSubtask, setAddingSubtask] = useState(false)
  const [confirmingSkip, setConfirmingSkip] = useState(false)

  // Page-level mutations (subtask create, skip, delete) are long-running and can
  // outlive the task they were started for — the same hazard `useScopedTaskUpdate`
  // guards for PATCHes. Every such operation takes a generation and the task id it
  // was issued for; only the newest operation, still on its own task, may publish
  // state, touch the save line, close the panel, or navigate.
  const latestOperationId = useRef(0)
  const activeTaskId = useRef(id)
  useLayoutEffect(() => {
    activeTaskId.current = id
    // Switching tasks retires every pending operation, including one issued for a
    // task we later switch back to.
    latestOperationId.current += 1
  }, [id])

  // The previous task's transient prompts belong to a task that is no longer on
  // screen; switching retires them along with its pending operations. Done during
  // render (the `useScopedTaskUpdate` pattern) rather than in an effect, so the
  // switch never renders one frame of the old task's composer.
  const [renderedTaskId, setRenderedTaskId] = useState(id)
  if (renderedTaskId !== id) {
    setRenderedTaskId(id)
    setAddingSubtask(false)
    setConfirmingSkip(false)
  }

  /** Start an operation; the returned predicate says whether it may still act. */
  function beginOperation(): () => boolean {
    const operationTaskId = id
    const operationId = ++latestOperationId.current
    return () =>
      operationId === latestOperationId.current && operationTaskId === activeTaskId.current
  }

  function applyUpdated(updated: Task) {
    // Final boundary: a response that outlived a switch to another task must
    // never become the surface's task, even if it slipped past the hook.
    if (updated.id !== id) return
    setTask(updated)
    setAllTasks((items) => items.map((item) => (item.id === updated.id ? updated : item)))
    onMutated?.()
  }

  const {
    saveState,
    saveError,
    setSaveState,
    setSaveError,
    savePatch,
    scopePromptOpen,
    resolveScope,
    cancelScope,
    reportError,
  } = useScopedTaskUpdate(task, applyUpdated)

  useEffect(() => {
    let active = true
    // include_closed so a task filed in a closed project still resolves its
    // project in the picker instead of showing blank.
    Promise.all([getTask(id), getSubtasks(id), listProjects(true), listAllTasks()])
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
          if (onClose) onClose()
          else navigate('/tasks', { replace: true })
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load task')
          setLoadedTaskId(id)
        }
      })
    return () => { active = false }
    // onClose is stable enough for the 404 escape hatch; re-fetch only per task.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, navigate])

  // Title and description are anchored separately: saving one must not discard
  // an edit in flight on the other (#255). Keyed by task id so a panel switch
  // never carries one task's typing into another.
  const titleField = useFieldDraft(task?.id ?? null, task?.title ?? '')
  const descriptionField = useFieldDraft(task?.id ?? null, task?.description ?? '')
  const titleDraft = titleField.value
  const descriptionDraft = descriptionField.value

  // Guard refresh/tab-close while a focused field holds an unsaved edit. In-app
  // navigation is already safe: clicking a <Link> blurs the field, which saves it.
  const dirty = task !== null && (titleField.dirty || descriptionField.dirty)
  useBeforeUnload(dirty)

  const parentOptions = useMemo(() => {
    if (!task) return []
    const blocked = descendantIds(task, allTasks)
    return allTasks.filter((candidate) => !blocked.has(candidate.id))
  }, [allTasks, task])

  async function handleSkip() {
    if (!task) return
    const isCurrent = beginOperation()
    setConfirmingSkip(false)
    setSaveState('saving')
    setSaveError(null)
    try {
      // Skip soft-deletes this occurrence and returns the next one; follow the
      // series forward so the user lands on the live task, not a deleted row.
      // In a panel, repoint with replace: Back must not land on the deleted row.
      const next = await skipOccurrence(task.id)
      if (!isCurrent()) return
      onMutated?.()
      if (panel) panel.openTask(next.id, { replace: true })
      else navigate(`/tasks/${next.id}`)
    } catch (e: unknown) {
      if (!isCurrent()) return
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to skip occurrence')
    }
  }

  function saveTitle() {
    if (!task) return
    const next = titleDraft.trim()
    if (!next) {
      reportError('Title is required')
      return
    }
    if (next !== task.title) savePatch({ title: next })
  }

  function saveDescription() {
    if (!task) return
    const next = descriptionDraft.trim() || null
    if (next !== task.description) savePatch({ description: next })
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.currentTarget.blur()
    }
  }

  async function handleAddSubtask(data: TaskCreate) {
    if (!task) return
    const isCurrent = beginOperation()
    const parentId = task.id
    setSaveState('saving')
    setSaveError(null)
    try {
      const created = await createUnscopedTask(data)
      if (!isCurrent()) return
      setSubtasks((items) => [...items, created])
      setAllTasks((items) => [...items, created])
      // The parent's estimate/status/has_subtasks are now derived — refresh it so
      // the read-only gating and rolled-up values reflect the new subtask.
      const refreshed = await getTask(parentId)
      // Two awaits, two chances to be outrun: re-check, and let applyUpdated make
      // the final id assertion before the refreshed parent becomes the surface.
      if (!isCurrent()) return
      applyUpdated(refreshed)
      setAddingSubtask(false)
      setSaveState('saved')
    } catch (e: unknown) {
      if (!isCurrent()) return
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to add subtask')
    }
  }

  async function handleDelete() {
    if (!task) return
    const isCurrent = beginOperation()
    setSaveState('saving')
    setSaveError(null)
    try {
      await deleteTask(task.id)
      // The trash count and the host list reflect the delete regardless of what
      // is on screen now; only the close/navigate belongs to the initiating task.
      void refreshTrashCount()
      onMutated?.()
      if (!isCurrent()) return
      if (onClose) onClose()
      else navigate('/tasks')
    } catch (e: unknown) {
      if (!isCurrent()) return
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to delete task')
    }
  }

  if (loadedTaskId !== id) return <p>Loading…</p>
  if (error) return <p role="alert">{error}</p>
  if (!task) return null

  const saveLabel = saveState === 'saving'
    ? 'Saving…'
    : saveState === 'saved'
      ? 'Saved'
      : saveState === 'error'
        ? 'Could not save'
        : ''

  return (
    <div className="task-detail">
      <div className="task-detail-header">
        <div className="task-detail-actions">
          {saveLabel && (
            <span
              className={saveState === 'error' ? 'save-state error' : 'save-state'}
              role={saveState === 'error' ? 'alert' : 'status'}
            >
              {saveLabel}
            </span>
          )}
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
        open={scopePromptOpen}
        onChoose={resolveScope}
        onCancel={cancelScope}
      />

      <section className="task-hero">
        <input
          className="task-title-input"
          aria-label="Task title"
          value={titleDraft}
          onChange={(e) => titleField.set(e.target.value)}
          onBlur={saveTitle}
          onKeyDown={handleTitleKeyDown}
        />
        <div className="task-card-badges">
          <StatusChip
            value={task.workflow_status}
            onChange={(status) => savePatch({ workflow_status: status })}
            disabled={task.has_subtasks}
            disabledHint="Rolled up from subtasks"
            onSkipOccurrence={
              task.repeat_interval && task.workflow_status !== 'done'
                ? () => setConfirmingSkip(true)
                : undefined
            }
          />
          {task.is_blocking && task.workflow_status !== 'done' && (
            <Badge tone="red">{blockingLabel(task.blocked_task_count)}</Badge>
          )}
          {!task.is_blocking && task.is_blocked && task.workflow_status !== 'done' && (
            <Badge tone="neutral">Blocked</Badge>
          )}
          <PriorityChip
            value={task.priority}
            onChange={(priority) => savePatch({ priority })}
          />
          <DueDateChip
            value={task.due_date}
            onChange={(due_date) => savePatch({ due_date })}
          />
          <EstimateChip
            value={task.estimated_minutes}
            onChange={(estimated_minutes) => savePatch({ estimated_minutes })}
            disabled={task.has_subtasks}
            disabledHint="Sum of subtask estimates"
          />
          <RepeatChip
            value={task.repeat_interval}
            onChange={(repeat_interval) => savePatch({ repeat_interval })}
            disabled={!task.due_date}
            disabledHint="Set a due date to enable recurrence"
          />
          {task.next_occurrence_date && (
            <span className="next-occurrence">
              next {formatDueDate(task.next_occurrence_date)}
            </span>
          )}
          <ProjectChip
            value={task.project_id}
            projects={projects}
            onChange={(project_id) => savePatch({ project_id })}
          />
          <ParentTaskChip
            value={task.parent_task_id}
            options={parentOptions.map((option) => ({ id: option.id, label: option.title }))}
            onChange={(parent_task_id) => savePatch({ parent_task_id })}
          />
        </div>
        {saveError && <p role="alert" className="error">{saveError}</p>}
      </section>

      <section className="task-detail-panel task-description-panel">
        <div className="task-section-heading">
          <h2>Description</h2>
        </div>
        <textarea
          aria-label="Task description"
          value={descriptionDraft}
          onChange={(e) => descriptionField.set(e.target.value)}
          onBlur={saveDescription}
          placeholder="Add a description"
          rows={5}
        />
      </section>

      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Subtasks</h2>
          <div className="task-section-actions">
            <button type="button" onClick={() => setAddingSubtask(true)}>
              <PlayCircle size={16} aria-hidden="true" />
              Add subtask
            </button>
          </div>
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
          <SubtaskComposer
            parent={task}
            onCreate={handleAddSubtask}
            onCancel={() => setAddingSubtask(false)}
          />
        )}
      </section>

      {task.recurrence_id && (
        <RecurrenceSeries
          task={task}
          onStopped={applyUpdated}
          onSkip={() => setConfirmingSkip(true)}
        />
      )}

      <TaskDependencies task={task} tasks={allTasks} />
    </div>
  )
}
