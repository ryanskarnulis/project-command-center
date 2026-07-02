import { type KeyboardEvent, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Circle, PlayCircle, SkipForward, Sparkles, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { breakDownTask, createUnscopedTask, deleteTask, getSubtasks, getTask, listAllTasks, reviewBreakdown, skipOccurrence } from '../../api/tasks'
import { ApiError } from '../../api/client'
import { decideCandidate } from '../../api/inbox'
import { listProjects } from '../../api/projects'
import { Badge } from '../../components/Badge'
import { useBeforeUnload } from '../../hooks/useBeforeUnload'
import type { Project } from '../../types/project'
import type { Task, TaskCreate } from '../../types/task'
import { BreakdownReview } from './BreakdownReview'
import { CandidateDecisionBar } from './CandidateDecisionBar'
import { EditScopeModal } from './EditScopeModal'
import { RecurrenceSeries } from './RecurrenceSeries'
import { SubtaskComposer } from './SubtaskComposer'
import { TaskCard } from './TaskCard'
import { TaskDependencies } from './TaskDependencies'
import { useTaskPanel } from './panel/taskPanelContext'
import { useScopedTaskUpdate } from './useScopedTaskUpdate'
import { useTrashCount } from '../trash/trashCountContext'
import { AssigneeChip } from './chips/AssigneeChip'
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

interface TaskDraft {
  source: string
  title: string
  description: string
}

const EMPTY_TASK_DRAFT: TaskDraft = {
  source: '',
  title: '',
  description: '',
}

function makeTaskDraft(task: Task): TaskDraft {
  const description = task.description ?? ''
  return {
    source: JSON.stringify([task.id, task.title, description]),
    title: task.title,
    description,
  }
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
  const [taskDraft, setTaskDraft] = useState<TaskDraft>(EMPTY_TASK_DRAFT)
  const [addingSubtask, setAddingSubtask] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [breakingDown, setBreakingDown] = useState(false)
  // Per-suggested-subtask in-flight guard so a double-click can't double-fire.
  const [decidingSubtaskId, setDecidingSubtaskId] = useState<number | null>(null)
  const [confirmingSkip, setConfirmingSkip] = useState(false)

  function applyUpdated(updated: Task) {
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

  const loadedTaskDraft = task ? makeTaskDraft(task) : EMPTY_TASK_DRAFT
  const activeTaskDraft =
    taskDraft.source === loadedTaskDraft.source ? taskDraft : loadedTaskDraft
  const titleDraft = activeTaskDraft.title
  const descriptionDraft = activeTaskDraft.description

  // Guard refresh/tab-close while a focused field holds an unsaved edit. In-app
  // navigation is already safe: clicking a <Link> blurs the field, which saves it.
  const dirty =
    task !== null &&
    (activeTaskDraft.title !== loadedTaskDraft.title ||
      activeTaskDraft.description !== loadedTaskDraft.description)
  useBeforeUnload(dirty)

  const parentOptions = useMemo(() => {
    if (!task) return []
    const blocked = descendantIds(task, allTasks)
    return allTasks.filter((candidate) => !blocked.has(candidate.id))
  }, [allTasks, task])

  async function handleSkip() {
    if (!task) return
    setConfirmingSkip(false)
    setSaveState('saving')
    setSaveError(null)
    try {
      // Skip soft-deletes this occurrence and returns the next one; follow the
      // series forward so the user lands on the live task, not a deleted row.
      // In a panel, repoint with replace: Back must not land on the deleted row.
      const next = await skipOccurrence(task.id)
      onMutated?.()
      if (panel) panel.openTask(next.id, { replace: true })
      else navigate(`/tasks/${next.id}`)
    } catch (e: unknown) {
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
      onMutated?.()
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
      onMutated?.()
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
      onMutated?.()
      if (onClose) onClose()
      else navigate('/tasks')
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
      onMutated?.()
      navigate(res.finalized ? '/inbox' : `/inbox/${inboxItemId}`)
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to record decision')
      setDeciding(false)
    }
  }

  if (loadedTaskId !== id) return <p>Loading…</p>
  if (error) return <p role="alert">{error}</p>
  if (!task) return null

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
    <div className="task-detail">
      <div className="task-detail-header">
        {isCandidate && (
          <p className="breadcrumb">
            <Link to={`/inbox/${task.inbox_item_id}`}>Open note review</Link>
          </p>
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
        open={scopePromptOpen}
        onChoose={resolveScope}
        onCancel={cancelScope}
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
          <StatusChip
            value={task.workflow_status}
            onChange={(status) => savePatch({ workflow_status: status })}
            disabled={task.has_subtasks}
            disabledHint="Rolled up from subtasks"
            onSkipOccurrence={
              !isCandidate && task.repeat_interval && task.workflow_status !== 'done'
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
          <ProjectChip
            value={task.project_id}
            projects={projects}
            onChange={(project_id) => savePatch({ project_id })}
            allowUnassigned={task.review_status === 'candidate'}
          />
          <AssigneeChip
            value={task.assignee_hint}
            onChange={(assignee_hint) => savePatch({ assignee_hint })}
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
          onChange={(e) =>
            setTaskDraft({ ...activeTaskDraft, description: e.target.value })
          }
          onBlur={saveDescription}
          placeholder="Add a description"
          rows={5}
        />
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
        <RecurrenceSeries task={task} onStopped={applyUpdated} />
      )}

      {!isCandidate && <TaskDependencies task={task} tasks={allTasks} />}
    </div>
  )
}
