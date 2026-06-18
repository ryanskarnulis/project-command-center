import { useState } from 'react'
import { Plus } from 'lucide-react'
import { useProjects } from './useProjects'
import { ProjectCard } from './ProjectCard'
import { ProjectFormModal } from './ProjectFormModal'
import type { Project } from '../../types/project'

export function ProjectsPage() {
  const { projects, loading, error, create, update, remove } = useProjects()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)

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
