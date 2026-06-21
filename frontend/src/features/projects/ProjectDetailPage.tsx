import { type FormEvent, type KeyboardEvent, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { getProjectSummary } from '../../api/dashboard'
import {
  createAlias,
  deleteAlias,
  getProject,
  listAliases,
  updateProject,
} from '../../api/projects'
import { listCompletedTasks, listTasks } from '../../api/tasks'
import type { ProjectSummary } from '../../types/dashboard'
import type { Project, ProjectAlias, ProjectUpdate } from '../../types/project'
import type { Task } from '../../types/task'
import { buildProjectStats } from '../../utils/projectStatus'
import { TaskCard } from '../tasks/TaskCard'
import { ActivityFeed } from './ActivityFeed'

export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const id = Number(projectId)
  const navigate = useNavigate()
  // When the user reached this page from the dashboard, the breadcrumb points
  // back there instead of the projects list. State is lost on refresh/direct
  // navigation, which safely falls back to "← Projects".
  const fromDashboard =
    (useLocation().state as { from?: string } | null)?.from === 'dashboard'

  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [doneCount, setDoneCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [descriptionDraft, setDescriptionDraft] = useState('')
  const [activityKey, setActivityKey] = useState(0)

  // AI summary (on-demand; 502-safe).
  const [summary, setSummary] = useState<ProjectSummary | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)

  // Aliases (managed in place via the dedicated alias endpoints).
  const [aliases, setAliases] = useState<ProjectAlias[]>([])
  const [newAlias, setNewAlias] = useState('')
  const [aliasBusy, setAliasBusy] = useState(false)
  const [aliasError, setAliasError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    setLoading(true)
    getProject(id)
      .then((p) => {
        if (!active) return
        setProject(p)
        setError(null)
      })
      .catch((e: unknown) => {
        if (!active) return
        if (e instanceof ApiError && e.status === 404) {
          navigate('/projects', { replace: true })
        } else {
          setError(e instanceof Error ? e.message : 'Failed to load project')
        }
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, navigate])

  // Load the project's open tasks separately so a task-fetch failure leaves the
  // rest of the project page usable.
  useEffect(() => {
    let active = true
    setTasksLoading(true)
    listTasks(id)
      .then((data) => {
        if (!active) return
        setTasks(data)
        setTasksError(null)
      })
      .catch((e: unknown) => {
        if (active) setTasksError(e instanceof Error ? e.message : 'Failed to load tasks')
      })
      .finally(() => { if (active) setTasksLoading(false) })
    return () => { active = false }
  }, [id])

  // Done count feeds the hero progress bar (best-effort).
  useEffect(() => {
    let active = true
    listCompletedTasks(id)
      .then((data) => { if (active) setDoneCount(data.length) })
      .catch(() => { /* best-effort */ })
    return () => { active = false }
  }, [id])

  useEffect(() => {
    let active = true
    listAliases(id)
      .then((data) => { if (active) setAliases(data) })
      .catch((e: unknown) => {
        if (active) setAliasError(e instanceof Error ? e.message : 'Failed to load aliases')
      })
    return () => { active = false }
  }, [id])

  useEffect(() => {
    if (!project) return
    setNameDraft(project.name)
    setDescriptionDraft(project.description ?? '')
  }, [project])

  async function savePatch(data: ProjectUpdate) {
    if (!project) return
    setSaveState('saving')
    setSaveError(null)
    try {
      const updated = await updateProject(project.id, data)
      setProject(updated)
      setSaveState('saved')
      setActivityKey((k) => k + 1)
    } catch (e: unknown) {
      setSaveState('error')
      setSaveError(e instanceof Error ? e.message : 'Failed to save project')
    }
  }

  function saveName() {
    if (!project) return
    const next = nameDraft.trim()
    if (!next) {
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

  async function handleSummarize() {
    setSummaryLoading(true)
    setSummaryError(null)
    try {
      setSummary(await getProjectSummary(id))
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 502) {
        setSummaryError('Summary unavailable — is Ollama running?')
      } else {
        setSummaryError(e instanceof Error ? e.message : 'Failed to summarize')
      }
    } finally {
      setSummaryLoading(false)
    }
  }

  async function handleAddAlias(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const value = newAlias.trim()
    if (!value || aliasBusy) return
    setAliasBusy(true)
    setAliasError(null)
    try {
      const created = await createAlias(id, { alias: value })
      setAliases((prev) => [...prev, created])
      setNewAlias('')
    } catch (e: unknown) {
      setAliasError(e instanceof Error ? e.message : 'Failed to add alias')
    } finally {
      setAliasBusy(false)
    }
  }

  async function handleRemoveAlias(aliasId: number) {
    setAliasBusy(true)
    setAliasError(null)
    try {
      await deleteAlias(id, aliasId)
      setAliases((prev) => prev.filter((a) => a.id !== aliasId))
    } catch (e: unknown) {
      setAliasError(e instanceof Error ? e.message : 'Failed to remove alias')
    } finally {
      setAliasBusy(false)
    }
  }

  if (loading) return <main className="task-detail"><div className="page-loading">Loading…</div></main>
  if (error) return <main className="task-detail"><p role="alert" className="error">{error}</p></main>
  if (!project) return null

  const stats = buildProjectStats(tasks, doneCount)
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
        <p className="breadcrumb">
          {fromDashboard ? (
            <Link to="/dashboard">← Dashboard</Link>
          ) : (
            <Link to="/projects">← Projects</Link>
          )}
        </p>
        <div className="task-detail-actions">
          {saveLabel && (
            <span
              className={saveState === 'error' ? 'save-state error' : 'save-state'}
              role={saveState === 'error' ? 'alert' : 'status'}
            >
              {saveLabel}
            </span>
          )}
        </div>
      </div>

      <section className="task-hero">
        <input
          className="task-title-input"
          aria-label="Project name"
          value={nameDraft}
          onChange={(e) => setNameDraft(e.target.value)}
          onBlur={saveName}
          onKeyDown={handleNameKeyDown}
        />
        <div className="task-card-badges">
          <span className={`status-pill tone-${stats.status.tone}`}>{stats.status.label}</span>
          <span className="estimate">{stats.open} open · {stats.done} done</span>
          {project.is_protected && <span className="source-pill">Protected</span>}
        </div>
        {stats.open + stats.done > 0 && (
          <div className="project-progress" aria-hidden="true">
            <span style={{ width: `${Math.round(stats.progress * 100)}%` }} />
          </div>
        )}
        {saveError && <p role="alert" className="error">{saveError}</p>}
      </section>

      <section className="task-detail-panel task-description-panel">
        <div className="task-section-heading">
          <h2>Description</h2>
        </div>
        <textarea
          aria-label="Project description"
          value={descriptionDraft}
          onChange={(e) => setDescriptionDraft(e.target.value)}
          onBlur={saveDescription}
          placeholder="Add a description"
          rows={5}
        />
      </section>

      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>AI Summary</h2>
          <button type="button" onClick={() => void handleSummarize()} disabled={summaryLoading}>
            {summaryLoading ? 'Summarizing…' : 'Summarize'}
          </button>
        </div>
        {summaryError && <p role="alert">{summaryError}</p>}
        {summary ? (
          <>
            <p className="project-summary-text">{summary.summary}</p>
            <small>Model: {summary.model_name}</small>
          </>
        ) : (
          !summaryLoading && !summaryError && (
            <p>No summary yet — generate one from this project's open tasks.</p>
          )
        )}
      </section>

      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Tasks</h2>
          <Link to={`/projects/${project.id}/tasks`}>View all tasks →</Link>
        </div>
        {tasksError && <p role="alert">{tasksError}</p>}
        {tasksLoading ? (
          <p>Loading…</p>
        ) : tasks.length > 0 ? (
          <ul className="task-detail-list">
            {tasks.map((t) => (
              <li key={t.id}><TaskCard task={t} /></li>
            ))}
          </ul>
        ) : (
          !tasksError && <p>No open tasks.</p>
        )}
      </section>

      <section className="task-detail-panel">
        <div className="task-section-heading">
          <h2>Aliases</h2>
        </div>
        <p>Names that map inbox text to this project when matching extracted tasks.</p>
        {aliases.length > 0 ? (
          <ul className="project-alias-list">
            {aliases.map((alias) => (
              <li key={alias.id}>
                <span>{alias.alias}</span>
                <button
                  type="button"
                  disabled={aliasBusy}
                  aria-label={`Remove alias ${alias.alias}`}
                  onClick={() => void handleRemoveAlias(alias.id)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p>No aliases yet.</p>
        )}
        <form className="project-alias-form" onSubmit={(e) => void handleAddAlias(e)}>
          <label htmlFor="pd-alias">Add alias</label>
          <input
            id="pd-alias"
            value={newAlias}
            onChange={(e) => setNewAlias(e.target.value)}
            placeholder="e.g. fw, firewall"
          />
          <button type="submit" disabled={aliasBusy || !newAlias.trim()}>Add</button>
        </form>
        {aliasError && <p role="alert">{aliasError}</p>}
      </section>

      <ActivityFeed projectId={project.id} refreshKey={activityKey} />
    </main>
  )
}
