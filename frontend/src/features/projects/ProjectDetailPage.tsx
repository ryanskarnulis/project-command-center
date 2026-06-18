import { type KeyboardEvent, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../../api/client'
import { getProject, updateProject } from '../../api/projects'
import { listTasks } from '../../api/tasks'
import type { Project, ProjectUpdate } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskCard } from '../tasks/TaskCard'

export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const id = Number(projectId)
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksLoading, setTasksLoading] = useState(true)
  const [tasksError, setTasksError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [descriptionDraft, setDescriptionDraft] = useState('')

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

  if (loading) return <main className="task-detail"><p>Loading…</p></main>
  if (error) return <main className="task-detail"><p role="alert" className="error">{error}</p></main>
  if (!project) return null

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
        <p className="breadcrumb"><Link to="/projects">← Projects</Link></p>
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
        {project.is_protected && (
          <div className="task-card-badges">
            <span className="source-pill">Protected</span>
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
    </main>
  )
}
