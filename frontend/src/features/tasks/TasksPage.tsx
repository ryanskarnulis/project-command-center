import { type FormEvent, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTasks } from './useTasks'
import type { TaskPriority } from '../../types/task'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']

export function TasksPage() {
  const { projectId } = useParams()
  const id = Number(projectId)
  const { tasks, loading, error, create, markDone, remove } = useTasks(id)

  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState<TaskPriority>('medium')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      await create({ title: title.trim(), priority })
      setTitle('')
      setPriority('medium')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main>
      <p>
        <Link to="/projects">← Projects</Link>
      </p>
      <h1>Tasks</h1>

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
            {t.status !== 'done' && (
              <button onClick={() => void markDone(t.id)}>Mark done</button>
            )}{' '}
            <button onClick={() => void remove(t.id)}>Delete</button>
          </li>
        ))}
      </ul>

      {!loading && tasks.length === 0 && <p>No tasks yet.</p>}
    </main>
  )
}
