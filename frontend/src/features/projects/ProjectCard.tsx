import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Project } from '../../types/project'

interface Props {
  project: Project
  actions?: ReactNode
}

export function ProjectCard({ project, actions }: Props) {
  return (
    <Link to={`/projects/${project.id}`} className="task-card" aria-label={project.name}>
      <div className="task-card-body">
        <span className="task-card-title">{project.name}</span>
        {project.description && (
          <span className="project-card-desc">{project.description}</span>
        )}
        {project.is_protected && (
          <div className="task-card-badges">
            <span className="source-pill">Protected</span>
          </div>
        )}
      </div>
      {actions && (
        <div className="task-card-actions" onClick={(e) => e.preventDefault()}>
          {actions}
        </div>
      )}
    </Link>
  )
}
