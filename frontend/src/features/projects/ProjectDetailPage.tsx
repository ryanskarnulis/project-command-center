import { type KeyboardEvent, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import {
  closeProject,
  deleteProject,
  getProject,
  reopenProject,
  updateProject,
} from '../../api/projects'
import { listCompletedTasks, listTasks, markTaskDone, updateTask } from '../../api/tasks'
import { useToast } from '../../components/ToastContext'
import type { Project, ProjectUpdate } from '../../types/project'
import type { Task, TaskUpdate, TaskWorkflowStatus } from '../../types/task'
import { useBeforeUnload } from '../../hooks/useBeforeUnload'
import { fireAndForget } from '../../utils/async'
import { buildProjectStats } from '../../utils/projectStatus'
import { TaskCard } from '../tasks/TaskCard'
import { SubtaskGroup } from '../tasks/SubtaskGroup'
import { TaskPanelProvider } from '../tasks/panel/TaskPanelProvider'
import { useTaskRefresh } from '../tasks/taskRefreshContext'
import { buildTaskTree } from '../tasks/taskTree'
import { useTrashCount } from '../trash/trashCountContext'
import { ActivityFeed } from './ActivityFeed'
import { ProjectTabs } from './ProjectTabs'

interface ProjectDraft {
  source: string
  name: string
  description: string
}

const EMPTY_PROJECT_DRAFT: ProjectDraft = { source: '', name: '', description: '' }

function makeProjectDraft(project: Project): ProjectDraft {
  const description = project.description ?? ''
  return {
    source: JSON.stringify([project.id, project.name, description]),
    name: project.name,
    description,
  }
}

export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const id = Number(projectId)
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksLoadedProjectId, setTasksLoadedProjectId] = useState<number | null>(null)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [doneCount, setDoneCount] = useState(0)
  const [loadedProjectId, setLoadedProjectId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  // Project the save status belongs to; the status is hidden once the route
  // moves to another project so a write for the old id can't label the new one.
  const [saveProjectId, setSaveProjectId] = useState<number | null>(null)
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>(EMPTY_PROJECT_DRAFT)
  const [activityKey, setActivityKey] = useState(0)
  // Bumped after a peek-panel mutation so the task list refetches behind it.
  const [tasksReloadKey, setTasksReloadKey] = useState(0)
  const { withToast } = useToast()
  const { refresh: refreshTrashCount } = useTrashCount()
  // Board drags can change tasks outside this page — refetch off the bump too.
  const { version: taskRefreshVersion } = useTaskRefresh()

  // Monotonic id of the most recently *started* project write. PATCH returns a
  // full project snapshot, so overlapping inline edits that resolve out of
  // order could otherwise let an older response revert a newer field; only the
  // newest write may publish its snapshot or its save state. Navigating to
  // another project retires the generation too (see the loader's cleanup).
  const latestRequestId = useRef(0)

  useEffect(() => {
    let active = true
    getProject(id)
      .then((p) => {
        if (!active) return
        setProject(p)
        setError(null)
        setLoadedProjectId(id)
      })
      .catch((e: unknown) => {
        if (!active) return
        if (e instanceof ApiError && e.status === 404) {
          navigate('/dashboard', { replace: true })
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load project')
          setLoadedProjectId(id)
        }
      })
    return () => {
      active = false
      // Only `:projectId` changes on a project→project navigation, so the
      // component (and `latestRequestId`) is reused. Retire the write
      // generation here so a PATCH started for the old id can never publish
      // its snapshot or its error into the new project's state.
      latestRequestId.current += 1
    }
  }, [id, navigate])

  // Load the project's open tasks separately so a task-fetch failure leaves the
  // rest of the project page usable.
  useEffect(() => {
    let active = true
    listTasks(id)
      .then((data) => {
        if (!active) return
        setTasks(data)
        setTasksError(null)
        setTasksLoadedProjectId(id)
      })
      .catch((e: unknown) => {
        if (!active) return
        setTasksError(e instanceof Error ? e.message : 'Failed to load tasks')
        setTasksLoadedProjectId(id)
      })
    return () => { active = false }
  }, [id, tasksReloadKey, taskRefreshVersion])

  // Done count feeds the hero progress bar (best-effort).
  useEffect(() => {
    let active = true
    listCompletedTasks(id)
      .then((data) => { if (active) setDoneCount(data.length) })
      .catch(() => { /* best-effort */ })
    return () => { active = false }
  }, [id, tasksReloadKey, taskRefreshVersion])

  const loadedProjectDraft = project ? makeProjectDraft(project) : EMPTY_PROJECT_DRAFT
  const activeProjectDraft =
    projectDraft.source === loadedProjectDraft.source ? projectDraft : loadedProjectDraft
  const nameDraft = activeProjectDraft.name
  const descriptionDraft = activeProjectDraft.description

  // Guard refresh/tab-close while a focused field holds an unsaved edit. In-app
  // navigation is already safe: clicking a <Link> blurs the field, which saves it.
  const dirty =
    project !== null &&
    (activeProjectDraft.name !== loadedProjectDraft.name ||
      activeProjectDraft.description !== loadedProjectDraft.description)
  useBeforeUnload(dirty)

  /** Publish a snapshot from a non-PATCH write (close/reopen), retiring any in-flight PATCH. */
  function publishProject(updated: Project) {
    latestRequestId.current += 1
    setProject(updated)
    setActivityKey((k) => k + 1)
  }

  async function savePatch(data: ProjectUpdate) {
    if (!project) return
    const targetId = project.id
    const requestId = ++latestRequestId.current
    setSaveProjectId(targetId)
    setSaveState('saving')
    setSaveError(null)
    try {
      const updated = await updateProject(targetId, data)
      if (requestId !== latestRequestId.current) return
      setProject(updated)
      setSaveState('saved')
      setActivityKey((k) => k + 1)
    } catch (e: unknown) {
      if (requestId !== latestRequestId.current) return
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to save project')
    }
  }

  function saveName() {
    if (!project) return
    const next = nameDraft.trim()
    if (!next) {
      setSaveProjectId(project.id)
      setSaveState('error')
      setSaveError('Name is required')
      return
    }
    if (next !== project.name) void savePatch({ name: next })
  }

  function saveDescription() {
    if (!project) return
    const next = descriptionDraft.trim() || null
    if (next !== project.description) void savePatch({ description: next })
  }

  function handleNameKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') e.currentTarget.blur()
  }

  async function handleDeleteProject(): Promise<void> {
    if (!project) return
    if (
      !window.confirm(
        `Delete “${project.name}”? Its active tasks move to Trash with it. You can restore them together.`,
      )
    ) {
      return
    }
    await withToast(deleteProject(project.id), { success: 'Project moved to trash' })
    void refreshTrashCount()
    navigate('/dashboard')
  }

  async function handleCloseProject(): Promise<void> {
    if (!project) return
    const updated = await withToast(closeProject(project.id), {
      success: 'Project closed',
    })
    publishProject(updated)
  }

  async function handleReopenProject(): Promise<void> {
    if (!project) return
    const updated = await withToast(reopenProject(project.id), {
      success: 'Project reopened',
    })
    publishProject(updated)
  }

  async function handleCompleteTask(t: Task): Promise<void> {
    await withToast(markTaskDone(t.id), { success: 'Task marked done' })
    setTasksReloadKey((k) => k + 1)
    setActivityKey((k) => k + 1)
  }

  async function handleUpdateTask(t: Task, patch: TaskUpdate): Promise<void> {
    await withToast(updateTask(t.id, patch), { success: 'Task saved' })
    setTasksReloadKey((k) => k + 1)
    setActivityKey((k) => k + 1)
  }

  // This list only shows open/in-progress tasks, so "done" is the only
  // transition needing a dedicated (recurrence-safe) endpoint.
  async function handleSetTaskStatus(t: Task, target: TaskWorkflowStatus): Promise<void> {
    if (target === 'done') {
      await handleCompleteTask(t)
    } else {
      await handleUpdateTask(t, { workflow_status: target })
    }
  }

  if (loadedProjectId !== id) {
    return <main className="task-detail"><div className="page-loading">Loading…</div></main>
  }
  if (error) return <main className="task-detail"><p role="alert" className="error">{error}</p></main>
  if (!project) return null

  const currentTasks = tasksLoadedProjectId === id ? tasks : []
  const tasksLoading = tasksLoadedProjectId !== id
  const stats = buildProjectStats(currentTasks, doneCount)
  const taskTree = buildTaskTree(currentTasks)
  const currentSaveState = saveProjectId === id ? saveState : 'idle'
  const currentSaveError = saveProjectId === id ? saveError : null
  const saveLabel = currentSaveState === 'saving'
    ? 'Saving…'
    : currentSaveState === 'saved'
      ? 'Saved'
      : currentSaveState === 'error'
        ? 'Could not save'
        : ''

  return (
    <TaskPanelProvider
      onMutated={() => {
        setTasksReloadKey((k) => k + 1)
        setActivityKey((k) => k + 1)
      }}
    >
    <main className="task-detail">
      <div className="task-detail-header">
        <p className="breadcrumb">
          <Link to="/dashboard">← Dashboard</Link>
        </p>
        <div className="task-detail-actions">
          {saveLabel && (
            <span
              className={currentSaveState === 'error' ? 'save-state error' : 'save-state'}
              role={currentSaveState === 'error' ? 'alert' : 'status'}
            >
              {saveLabel}
            </span>
          )}
          {!project.is_protected && (
            <>
              <button
                type="button"
                onClick={() =>
                  fireAndForget(
                    project.closed_at ? handleReopenProject() : handleCloseProject(),
                  )
                }
              >
                {project.closed_at ? 'Reopen project' : 'Close project'}
              </button>
              <button
                type="button"
                className="danger-action"
                onClick={() => fireAndForget(handleDeleteProject())}
              >
                Delete project
              </button>
            </>
          )}
        </div>
      </div>

      <section className="task-hero">
        <input
          className="task-title-input"
          aria-label="Project name"
          value={nameDraft}
          onChange={(e) =>
            setProjectDraft({ ...activeProjectDraft, name: e.target.value })
          }
          onBlur={saveName}
          onKeyDown={handleNameKeyDown}
        />
        <div className="task-card-badges">
          <span className={`status-pill tone-${stats.status.tone}`}>{stats.status.label}</span>
          <span className="estimate">{stats.open} open · {stats.done} done</span>
          {project.is_protected && <span className="source-pill">Protected</span>}
          {project.closed_at && <span className="source-pill">Closed</span>}
        </div>
        {stats.open + stats.done > 0 && (
          <div className="project-progress" aria-hidden="true">
            <span style={{ width: `${Math.round(stats.progress * 100)}%` }} />
          </div>
        )}
        {currentSaveError && <p role="alert" className="error">{currentSaveError}</p>}
      </section>

      <ProjectTabs projectId={project.id} />

      <section className="task-detail-panel task-description-panel">
        <div className="task-section-heading">
          <h2>Description</h2>
        </div>
        <textarea
          aria-label="Project description"
          value={descriptionDraft}
          onChange={(e) =>
            setProjectDraft({ ...activeProjectDraft, description: e.target.value })
          }
          onBlur={saveDescription}
          placeholder="Add a description"
          rows={5}
        />
      </section>

      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Tasks</h2>
          <Link to={`/projects/${project.id}/tasks`}>View all tasks →</Link>
        </div>
        {tasksError && <p role="alert">{tasksError}</p>}
        {tasksLoading ? (
          <p>Loading…</p>
        ) : currentTasks.length > 0 ? (
          <ul className="task-detail-list">
            {taskTree.roots.map((t) => (
              <li key={t.id}>
                <TaskCard
                  task={t}
                  onComplete={() => fireAndForget(handleCompleteTask(t))}
                  onUpdate={(patch) => fireAndForget(handleUpdateTask(t, patch))}
                  onSetStatus={(target) =>
                    fireAndForget(handleSetTaskStatus(t, target))
                  }
                />
                <SubtaskGroup
                  children={taskTree.childrenOf.get(t.id) ?? []}
                  onCompleteTask={(s) => fireAndForget(handleCompleteTask(s))}
                  onUpdateTask={(s, patch) =>
                    fireAndForget(handleUpdateTask(s, patch))
                  }
                  onSetTaskStatus={(s, target) =>
                    fireAndForget(handleSetTaskStatus(s, target))
                  }
                />
              </li>
            ))}
          </ul>
        ) : (
          !tasksError && <p>No open tasks.</p>
        )}
      </section>

      <ActivityFeed projectId={project.id} refreshKey={activityKey} />
    </main>
    </TaskPanelProvider>
  )
}
