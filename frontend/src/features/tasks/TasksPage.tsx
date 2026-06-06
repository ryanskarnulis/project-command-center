import { type FormEvent, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { listProjects } from '../../api/projects'
import type { Project } from '../../types/project'
import type { Task, TaskPriority, TaskStatus } from '../../types/task'
import { compareTasks, dueStatus } from '../../utils/dates'
import { ActivityFeed } from '../projects/ActivityFeed'
import { TaskCard } from './TaskCard'
import { TaskFormModal } from './TaskFormModal'
import { useTasks } from './useTasks'

interface Filters {
  status: TaskStatus | ''
  priority: TaskPriority | ''
  projectId: number | ''
  overdue: boolean
  dueSoon: boolean
  blocked: boolean
}

const EMPTY_FILTERS: Filters = {
  status: '',
  priority: '',
  projectId: '',
  overdue: false,
  dueSoon: false,
  blocked: false,
}

function isActive(f: Filters): boolean {
  return (
    f.status !== '' ||
    f.priority !== '' ||
    f.projectId !== '' ||
    f.overdue ||
    f.dueSoon ||
    f.blocked
  )
}

function matchesFilters(t: Task, f: Filters): boolean {
  if (f.status && t.status !== f.status) return false
  if (f.priority && t.priority !== f.priority) return false
  if (f.projectId !== '' && t.project_id !== f.projectId) return false
  if (f.overdue && dueStatus(t.due_date) !== 'overdue') return false
  if (f.dueSoon && !['today', 'soon'].includes(dueStatus(t.due_date))) return false
  if (f.blocked && !t.is_blocked) return false
  return true
}

export function TasksPage() {
  const { projectId } = useParams()
  const id = projectId === undefined ? undefined : Number(projectId)
  const isGlobal = id === undefined
  const { tasks, loading, error, create, update, markDone, remove } = useTasks(id)

  const [addingTask, setAddingTask] = useState(false)
  const [editing, setEditing] = useState<Task | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS)

  // Per-row subtask composer
  const [subtaskParentId, setSubtaskParentId] = useState<number | null>(null)
  const [subtaskTitle, setSubtaskTitle] = useState('')

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {})
  }, [])
  const [activityKey, setActivityKey] = useState(0)
  const bumpActivity = () => setActivityKey((k) => k + 1)

  // Filter flat list first, then build tree. A child that matches while its parent
  // doesn't is promoted to root — same behaviour as orphaned-parent fallback.
  const { roots, childrenOf } = useMemo(() => {
    const filtered = isActive(filters)
      ? tasks.filter((t) => matchesFilters(t, filters))
      : tasks
    const ids = new Set(filtered.map((t) => t.id))
    const childrenOf = new Map<number, Task[]>()
    const roots: Task[] = []
    for (const t of filtered) {
      if (t.parent_task_id !== null && ids.has(t.parent_task_id)) {
        const group = childrenOf.get(t.parent_task_id) ?? []
        group.push(t)
        childrenOf.set(t.parent_task_id, group)
      } else {
        roots.push(t)
      }
    }
    return { roots, childrenOf }
  }, [tasks, filters])

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

  const filtersActive = isActive(filters)

  return (
    <main>
      {!isGlobal && (
        <p>
          <Link to="/projects">← Projects</Link>
        </p>
      )}
      <h1>{isGlobal ? 'Open Tasks' : 'Tasks'}</h1>

      <button type="button" onClick={() => setAddingTask(true)}>Add task</button>

      <div className="task-filters" role="search" aria-label="Filter tasks">
        <select
          aria-label="Filter by status"
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value as TaskStatus | '' }))}
        >
          <option value="">All statuses</option>
          <option value="candidate">Candidate</option>
          <option value="accepted">Accepted</option>
          <option value="done">Done</option>
          <option value="rejected">Rejected</option>
        </select>

        <select
          aria-label="Filter by priority"
          value={filters.priority}
          onChange={(e) => setFilters((f) => ({ ...f, priority: e.target.value as TaskPriority | '' }))}
        >
          <option value="">All priorities</option>
          <option value="urgent">Urgent</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        {isGlobal && (
          <select
            aria-label="Filter by project"
            value={filters.projectId === '' ? '' : String(filters.projectId)}
            onChange={(e) =>
              setFilters((f) => ({ ...f, projectId: e.target.value === '' ? '' : Number(e.target.value) }))
            }
          >
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.id} value={String(p.id)}>{p.name}</option>
            ))}
          </select>
        )}

        <label>
          <input
            type="checkbox"
            checked={filters.overdue}
            onChange={(e) => setFilters((f) => ({ ...f, overdue: e.target.checked }))}
          />
          {' '}Overdue
        </label>

        <label>
          <input
            type="checkbox"
            checked={filters.dueSoon}
            onChange={(e) => setFilters((f) => ({ ...f, dueSoon: e.target.checked }))}
          />
          {' '}Due soon
        </label>

        <label>
          <input
            type="checkbox"
            checked={filters.blocked}
            onChange={(e) => setFilters((f) => ({ ...f, blocked: e.target.checked }))}
          />
          {' '}Blocked
        </label>

        {filtersActive && (
          <button type="button" onClick={() => setFilters(EMPTY_FILTERS)}>
            Clear filters
          </button>
        )}
      </div>

      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}

      <ul>{[...roots].sort(compareTasks).map(renderTask)}</ul>

      {!loading && roots.length === 0 && (
        <p>{filtersActive ? 'No tasks match the current filters.' : 'No tasks yet.'}</p>
      )}

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
