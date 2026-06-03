import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task, TaskPriority } from '../../types/task'
import { compareByDue, dueStatus, formatDueDate } from '../../utils/dates'
import { ActivityFeed } from '../projects/ActivityFeed'
import { TaskEditModal } from './TaskEditModal'
import { useTasks } from './useTasks'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, update, markDone, remove } = useTasks(id)

  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState<TaskPriority>('medium')
  const [submitting, setSubmitting] = useState(false)
  const [editing, setEditing] = useState<Task | null>(null)
  const [projects, setProjects] = useState<Project[]>([])

  // Per-row subtask composer: the parent id we're adding under, plus its title.
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)
  const [subtaskTitle, setSubtaskTitle] = useState('')

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {})
  }, [])
  // Bumped after any task mutation so the ActivityFeed re-fetches.
  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  // Group tasks by parent so we can render the tree. A task whose parent isn't in
  // the current (filtered) set — e.g. a since-deleted parent — renders at the root.
  const { roots, childrenOf } = useMemo(() => {
    const ids = new Set(tasks.map((t) => t.id))
    const childrenOf = new Map<number, Task[]>()
    const roots: Task[] = []
    for (const t of tasks) {
      if (t.parent_task_id !== null && ids.has(t.parent_task_id)) {
        const group = childrenOf.get(t.parent_task_id) ?? []
        group.push(t)
        childrenOf.set(t.parent_task_id, group)
      } else {
        roots.push(t)
      }
    }
    return { roots, childrenOf }
  }, [tasks])

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
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

  async function handleAddSubtask(e: FormEvent<HTMLFormElement>, parentId: number) {
    e.preventDefault()
    if (!subtaskTitle.trim()) return
    await create({ title: subtaskTitle.trim(), parent_task_id: parentId })
    setSubtaskTitle('')
    setSubtaskParentId(null)
    bumpActivity()
  }

  function renderTask(t: Task) {
    const kids = [...(childrenOf.get(t.id) ?? [])].sort(compareByDue)
    return (
      <li key={t.id}>
        <span>{t.title}</span> <span>[{t.status}]</span>{' '}
        <span>({t.priority})</span>{' '}
        {t.due_date && t.status !== 'done' && (
          <span className={`due due-${dueStatus(t.due_date)}`}>
            Due {formatDueDate(t.due_date)}
          </span>
        )}{' '}
        {isGlobal && t.project_id !== null && (
          <Link to={`/projects/${t.project_id}/tasks`}>Project #{t.project_id}</Link>
        )}{' '}
        <button onClick={() => setEditing(t)}>Edit</button>{' '}
        <button
          onClick={() => {
            setSubtaskParentId(t.id)
            setSubtaskTitle('')
          }}
        >
          Add subtask
        </button>{' '}
        {t.status !== 'done' && (
          <button onClick={() => void markDone(t.id).then(bumpActivity)}>
            Mark done
          </button>
        )}{' '}
        <button onClick={() => void remove(t.id).then(bumpActivity)}>Delete</button>
        {subtaskParentId === t.id && (
          <form onSubmit={(e) => void handleAddSubtask(e, t.id)}>
            <input
              autoFocus
              value={subtaskTitle}
              onChange={(e) => setSubtaskTitle(e.target.value)}
              placeholder="Subtask title"
            />
            <button type="submit" disabled={!subtaskTitle.trim()}>
              Add
            </button>{' '}
            <button type="button" onClick={() => setSubtaskParentId(null)}>
              Cancel
            </button>
          </form>
        )}
        {kids.length > 0 && (
          <ul className="task-children">{kids.map(renderTask)}</ul>
        )}
      </li>
    )
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

      <ul>{[...roots].sort(compareByDue).map(renderTask)}</ul>

      {!loading && tasks.length === 0 && <p>No tasks yet.</p>}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}

      {editing && (
        <TaskEditModal
          task={editing}
          tasks={tasks}
          projects={projects}
          onClose={() => setEditing(null)}
          onSave={async (taskId, data) => {
            await update(taskId, data)
            bumpActivity()
          }}
        />
      )}
    </main>
  )
}
