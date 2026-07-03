import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { Task } from '../../types/task'
import type { Project } from '../../types/project'
import { TaskCard } from './TaskCard'

interface SubtaskGroupProps {
  children: Task[]
  projects?: Project[]
  /** Enables the one-click complete circle on each subtask card. */
  onCompleteTask?: (task: Task) => void
}

/**
 * Read-only collapsible "Subtasks (n)" group rendered beneath a parent task.
 * Mirrors the `.task-subtasks` / `.task-children` markup used by the global task
 * list so subtasks look and behave consistently wherever they appear.
 */
export function SubtaskGroup({ children, projects, onCompleteTask }: SubtaskGroupProps) {
  const [expanded, setExpanded] = useState(false)
  if (children.length === 0) return null
  return (
    <div className="task-subtasks">
      <button
        type="button"
        className="task-subtasks-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? (
          <ChevronDown size={16} aria-hidden="true" />
        ) : (
          <ChevronRight size={16} aria-hidden="true" />
        )}
        <span>Subtasks ({children.length})</span>
      </button>
      {expanded && (
        <ul className="task-children">
          {children.map((t) => (
            <li key={t.id}>
              <TaskCard
                task={t}
                projects={projects}
                onComplete={onCompleteTask && (() => onCompleteTask(t))}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
