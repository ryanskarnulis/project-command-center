import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { compareTasks } from '../../utils/dates'
import { ActivityFeed } from '../projects/ActivityFeed'
import { TaskCard } from './TaskCard'
import { TaskFormModal } from './TaskFormModal'
import { useTasks } from './useTasks'

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, update, markDone, remove } = useTasks(id)

  const [addingTask, setAddingTask] = useState(false)
  const [editing, setEditing] = useState<Task | null>(null)
  const [projects, setProjects] = useState<Project[]>([])

  // Per-row subtask composer: the parent id we're adding under, plus its title.
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)
  const [subtaskTitle, setSubtaskTitle] = useState('')

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {})
  }, [])
  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  // Group tasks by parent. A task whose parent isn't in the current set renders at root.
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

  async function handleAddSubtask(e: FormEvent<HTMLFormElement>, parentId: number) {
    e.preventDefault()
    if (!subtaskTitle.trim()) return
    await create({ title: subtaskTitle.trim(), parent_task_id: parentId })
    setSubtaskTitle('')
    setSubtaskParentId(null)
    bumpActivity()
  }

  function renderTask(t: Task) {
    const kids = [...(childrenOf.get(t.id) ?? [])].sort(compareTasks)
    const actions = (
      <>
        <button onClick={() => setEditing(t)}>Edit</button>
        <button
          onClick={() => {
            setSubtaskParentId(t.id)
            setSubtaskTitle('')
          }}
        >
          Add subtask
        </button>
        {t.status !== 'done' && (
          <button onClick={() => void markDone(t.id).then(bumpActivity)}>
            Mark done
          </button>
        )}
        <button onClick={() => void remove(t.id).then(bumpActivity)}>Delete</button>
      </>
    )
    return (
      <li key={t.id}>
        <TaskCard
          task={t}
          projects={isGlobal ? projects : undefined}
          actions={actions}
        />
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

      <button type="button" onClick={() => setAddingTask(true)}>Add task</button>

      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}

      <ul>{[...roots].sort(compareTasks).map(renderTask)}</ul>

      {!loading && tasks.length === 0 && <p>No tasks yet.</p>}

      {!isGlobal && <ActivityFeed projectId={id} refreshKey={activityKey} />}

      {addingTask && (
        <TaskFormModal
          mode="create"
          tasks={tasks}
          projects={projects}
          onClose={() => setAddingTask(false)}
          onSave={async (data) => {
            await create(data)
            bumpActivity()
          }}
        />
      )}

      {editing && (
        <TaskFormModal
          mode="edit"
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
