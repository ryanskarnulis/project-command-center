import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getSubtasks, getTask, updateTask } from '../../api/tasks'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task, TaskUpdate } from '../../types/task'
import { TaskCard } from './TaskCard'
import { TaskFormModal } from './TaskFormModal'
import { TaskDependencies } from './TaskDependencies'

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
  const [editing, setEditing] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([getTask(id), getSubtasks(id), listProjects()])
      .then(([t, subs, projs]) => {
        if (!active) return
        setTask(t)
        setSubtasks(subs)
        setProjects(projs)
        setAllTasks([t, ...subs])
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
  }, [id, refreshKey, navigate])

  async function handleSave(taskId: number, data: TaskUpdate) {
    await updateTask(taskId, data)
    setRefreshKey((k) => k + 1)
    setEditing(false)
  }

  if (loading) return <main><p>Loading…</p></main>
  if (error) return <main><p role="alert">{error}</p></main>
  if (!task) return null

  return (
    <main>
      <p><Link to="/tasks">← Open Tasks</Link></p>

      <h1>{task.title}</h1>

      <button type="button" onClick={() => setEditing(true)}>Edit</button>

      {task.description && <p>{task.description}</p>}

      <dl>
        <dt>Status</dt><dd>{task.status}</dd>
        <dt>Priority</dt><dd>{task.priority}</dd>
        {task.due_date && <><dt>Due</dt><dd>{task.due_date}</dd></>}
        {task.estimated_minutes != null && (
          <><dt>Estimate</dt><dd>{task.estimated_minutes} min</dd></>
        )}
      </dl>

      {subtasks.length > 0 && (
        <section>
          <h2>Subtasks</h2>
          <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {subtasks.map((s) => (
              <li key={s.id}>
                <TaskCard task={s} projects={projects} />
              </li>
            ))}
          </ul>
        </section>
      )}

      <TaskDependencies task={task} tasks={allTasks} />

      {editing && (
        <TaskFormModal
          mode="edit"
          task={task}
          tasks={allTasks}
          projects={projects}
          onClose={() => setEditing(false)}
          onSave={handleSave}
        />
      )}
    </main>
  )
}
