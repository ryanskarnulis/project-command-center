import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { Project } from '../../types/project'
import type { ProjectStats } from '../../utils/projectStatus'

interface Props {
  project: Project
  stats?: ProjectStats
  actions?: ReactNode
}

export function ProjectCard({ project, stats, actions }: Props) {
  return (
    <Link to={`/projects/${project.id}`} className="task-card" aria-label={project.name}>
      <div className="task-card-body">
        <span className="task-card-title">{project.name}</span>
        {project.description && (
          <span className="project-card-desc">{project.description}</span>
        )}
        <div className="task-card-badges">
          {stats && (
            <span className={`status-pill tone-${stats.status.tone}`}>
              {stats.status.label}
            </span>
          )}
          {stats && (
            <span className="estimate">{stats.open} open · {stats.done} done</span>
          )}
          {project.is_protected && <span className="source-pill">Protected</span>}
        </div>
        {stats && stats.open + stats.done > 0 && (
          <div className="project-progress" aria-hidden="true">
            <span style={{ width: `${Math.round(stats.progress * 100)}%` }} />
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
