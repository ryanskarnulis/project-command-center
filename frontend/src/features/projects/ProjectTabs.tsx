import { NavLink } from 'react-router-dom'

/**
 * The per-project surface tab strip: Overview · Tasks. Plain `NavLink`s so the
 * active route gets `aria-current`/active styling for free; no nav library.
 * Shared across the project detail and tasks routes so the two read as one
 * surface.
 */
export function ProjectTabs({ projectId }: { projectId: number }) {
  return (
    <nav className="project-tabs" aria-label="Project sections">
      <NavLink to={`/projects/${projectId}`} end className="project-tab">
        Overview
      </NavLink>
      <NavLink to={`/projects/${projectId}/tasks`} className="project-tab">
        Tasks
      </NavLink>
    </nav>
  )
}
