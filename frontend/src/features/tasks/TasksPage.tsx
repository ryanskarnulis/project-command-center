import { type SubmitEvent, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTasks } from './useTasks'
import { ActivityFeed } from '../projects/ActivityFeed'
import type { TaskPriority } from '../../types/task'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, markDone, remove } = useTasks(id)

  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState<TaskPriority>('medium')
  const [submitting, setSubmitting] = useState(false)
  // Bumped after any task mutation so the ActivityFeed re-fetches.
  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      await create({ title: title.trim(), priority })
      setTitle('')
      setPriority('medium')
      bumpActivity()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main>
      {!isGlobal && (
        <p>
          <Link to="/projects">← Projects</Link>
        </p>
      )}
      <h1>{isGlobal ? 'Open Tasks' : 'Tasks'}</h1>

      <form onSubmit={handleSubmit}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Task title"
        />
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value as TaskPriority)}
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <button type="submit" disabled={submitting || !title.trim()}>
          Add task
        </button>
      </form>

      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}

      <ul>
        {tasks.map((t) => (
          <li key={t.id}>
            <span>{t.title}</span> <span>[{t.status}]</span>{' '}
            <span>({t.priority})</span>{' '}
            {isGlobal && t.project_id !== null && (
              <Link to={`/projects/${t.project_id}/tasks`}>Project #{t.project_id}</Link>
            )}{' '}
            {t.status !== 'done' && (
              <button
                onClick={() => void markDone(t.id).then(bumpActivity)}
              >
                Mark done
              </button>
            )}{' '}
            <button onClick={() => void remove(t.id).then(bumpActivity)}>
              Delete
            </button>
          </li>
        ))}
      </ul>

      {!loading && tasks.length === 0 && <p>No tasks yet.</p>}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}
    </main>
  )
}
