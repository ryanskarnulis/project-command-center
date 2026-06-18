import { useEffect, useMemo, useState } from 'react'
import { Plus, Search, SlidersHorizontal } from 'lucide-react'
import { useProjects } from './useProjects'
import { ProjectCard } from './ProjectCard'
import { ProjectFormModal } from './ProjectFormModal'
import { listAllTasks, listCompletedTasks } from '../../api/tasks'
import { buildProjectStats, type ProjectStats } from '../../utils/projectStatus'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'

type SortMode = 'name' | 'open' | 'updated' | 'created'

export function ProjectsPage() {
  const { projects, loading, error, create, update, remove } = useProjects()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [search, setSearch] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>('name')

  // Per-project stats are best-effort: reuse the same task lists the dashboard
  // loads, grouped client-side. A fetch failure just leaves cards without stats.
  const [openTasks, setOpenTasks] = useState<Task[]>([])
  const [doneTasks, setDoneTasks] = useState<Task[]>([])

  useEffect(() => {
    let active = true
    Promise.all([listAllTasks(), listCompletedTasks()])
      .then(([open, done]) => {
        if (!active) return
        setOpenTasks(open)
        setDoneTasks(done)
      })
      .catch(() => { /* stats are best-effort; cards still render without them */ })
    return () => { active = false }
  }, [])

  const statsByProject = useMemo(() => {
    const openByProject = new Map<number, Task[]>()
    for (const t of openTasks) {
      if (t.project_id === null) continue
      const arr = openByProject.get(t.project_id) ?? []
      arr.push(t)
      openByProject.set(t.project_id, arr)
    }
    const doneByProject = new Map<number, number>()
    for (const t of doneTasks) {
      if (t.project_id === null) continue
      doneByProject.set(t.project_id, (doneByProject.get(t.project_id) ?? 0) + 1)
    }
    const map = new Map<number, ProjectStats>()
    for (const p of projects) {
      map.set(
        p.id,
        buildProjectStats(openByProject.get(p.id) ?? [], doneByProject.get(p.id) ?? 0),
      )
    }
    return map
  }, [openTasks, doneTasks, projects])

  const visibleProjects = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filtered = q
      ? projects.filter(
          (p) =>
            p.name.toLowerCase().includes(q) ||
            (p.description ?? '').toLowerCase().includes(q),
        )
      : projects
    return [...filtered].sort((a, b) => {
      switch (sortMode) {
        case 'open':
          return (statsByProject.get(b.id)?.open ?? 0) - (statsByProject.get(a.id)?.open ?? 0)
        case 'updated':
          return b.updated_at.localeCompare(a.updated_at)
        case 'created':
          return b.created_at.localeCompare(a.created_at)
        case 'name':
        default:
          return a.name.localeCompare(b.name)
      }
    })
  }, [projects, search, sortMode, statsByProject])

  const filtersActive = search.trim() !== '' || sortMode !== 'name'

  function handleDelete(p: Project) {
    if (window.confirm(`Delete "${p.name}"? Its active tasks move to General.`)) {
      void remove(p.id)
    }
  }

  return (
    <main>
      <div className="section-heading">
        <h1>Projects</h1>
        <button type="button" onClick={() => setCreating(true)}>
          <Plus size={16} aria-hidden="true" />
          New project
        </button>
      </div>

      {loading && <div className="page-loading">Loading projects…</div>}
      {error && <p role="alert" className="error">{error}</p>}

      {!loading && projects.length === 0 && (
        <div className="empty-state">No projects yet.</div>
      )}

      {!loading && projects.length > 0 && (
        <>
          <div className="task-filters" role="search" aria-label="Filter projects">
            <div className="task-filters-header">
              <div className="task-filters-title">
                <SlidersHorizontal size={17} aria-hidden="true" />
                <strong>Filters</strong>
              </div>
              {filtersActive && (
                <button
                  type="button"
                  className="secondary-action"
                  onClick={() => {
                    setSearch('')
                    setSortMode('name')
                  }}
                >
                  Clear
                </button>
              )}
            </div>

            <label className="task-search-field">
              <span>Search</span>
              <div>
                <Search size={17} aria-hidden="true" />
                <input
                  aria-label="Search projects"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Name or description"
                />
              </div>
            </label>

            <div className="task-filter-grid">
              <label>
                <span>Sort</span>
                <select
                  aria-label="Sort projects"
                  value={sortMode}
                  onChange={(e) => setSortMode(e.target.value as SortMode)}
                >
                  <option value="name">Name</option>
                  <option value="open">Most open tasks</option>
                  <option value="updated">Recently updated</option>
                  <option value="created">Recently created</option>
                </select>
              </label>
            </div>
          </div>

          {visibleProjects.length === 0 ? (
            <div className="empty-state">No projects match your search.</div>
          ) : (
            <ul className="project-grid">
              {visibleProjects.map((p) => (
                <li key={p.id}>
                  <ProjectCard
                    project={p}
                    stats={statsByProject.get(p.id)}
                    actions={
                      <>
                        <button type="button" onClick={() => setEditing(p)}>Edit</button>
                        {!p.is_protected && (
                          <button
                            type="button"
                            className="danger-action"
                            onClick={() => handleDelete(p)}
                          >
                            Delete
                          </button>
                        )}
                      </>
                    }
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {creating && (
        <ProjectFormModal
          mode="create"
          onClose={() => setCreating(false)}
          onSave={create}
        />
      )}
      {editing && (
        <ProjectFormModal
          mode="edit"
          project={editing}
          onClose={() => setEditing(null)}
          onSave={update}
        />
      )}
    </main>
  )
}
