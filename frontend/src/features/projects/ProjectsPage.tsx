import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { useProjects } from './useProjects'
import { ProjectEditModal } from './ProjectEditModal'
import type { Project } from '../../types/project'

export function ProjectsPage() {
  const { projects, loading, error, create, update, remove } = useProjects()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    try {
      await create({
        name: name.trim(),
        description: description.trim() || null,
      })
      setName('')
      setDescription('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main>
      <h1>Projects</h1>

      <form onSubmit={handleSubmit}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Project name"
        />
        <input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
        />
        <button type="submit" disabled={submitting || !name.trim()}>
          Add project
        </button>
      </form>

      {loading && <p>Loading…</p>}
      {error && <p role="alert">{error}</p>}

      <ul>
        {projects.map((p) => (
          <li key={p.id}>
            <Link to={`/projects/${p.id}`}>{p.name}</Link>
            {p.description && <span> — {p.description}</span>}{' '}
            <button onClick={() => setEditing(p)}>Edit</button>{' '}
            {p.is_protected ? (
              <span>Protected</span>
            ) : (
              <button onClick={() => void remove(p.id)}>Delete</button>
            )}
          </li>
        ))}
      </ul>

      {!loading && projects.length === 0 && <p>No projects yet.</p>}

      {editing && (
        <ProjectEditModal
          project={editing}
          onClose={() => setEditing(null)}
          onSave={update}
        />
      )}
    </main>
  )
}
