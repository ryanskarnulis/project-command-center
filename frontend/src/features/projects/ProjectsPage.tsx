import { useEffect, useMemo, useState } from 'react'
import { Plus } from 'lucide-react'
import { useProjects } from './useProjects'
import { ProjectCard } from './ProjectCard'
import { ProjectFormModal } from './ProjectFormModal'
import { listAllTasks, listCompletedTasks } from '../../api/tasks'
import { buildProjectStats, type ProjectStats } from '../../utils/projectStatus'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'

export function ProjectsPage() {
  const { projects, loading, error, create, update, remove } = useProjects()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)

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

  return (
    <main>
      <div className="section-heading">
        <h1>Projects</h1>
        <button type="button" onClick={() => setCreating(true)}>
          <Plus size={16} aria-hidden="true" />
          New project
        </button>
      </div>

      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}

      {!loading && projects.length === 0 ? (
        <p>No projects yet.</p>
      ) : (
        <ul className="project-grid">
          {projects.map((p) => (
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
                        onClick={() => void remove(p.id)}
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
