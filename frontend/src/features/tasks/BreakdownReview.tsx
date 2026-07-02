import { CheckCircle2, Trash2 } from 'lucide-react'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskCard } from './TaskCard'

interface BreakdownReviewProps {
  /** Candidate subtasks from "break this down", awaiting approve/dismiss. */
  suggestions: Task[]
  projects: Project[]
  /** Id of the suggestion whose decision is in flight (buttons disabled). */
  decidingId: number | null
  onDecide: (subtaskId: number, action: 'approve' | 'dismiss') => void
}

/** The AI-suggested-subtasks review block on the task detail page. */
export function BreakdownReview({
  suggestions,
  projects,
  decidingId,
  onDecide,
}: BreakdownReviewProps) {
  if (suggestions.length === 0) return null
  return (
    <div className="task-suggested-subtasks">
      <p className="task-suggested-lead">
        Suggested subtasks — approve the ones you want, dismiss the rest.
      </p>
      <ul className="task-detail-list">
        {suggestions.map((s) => (
          <li key={s.id}>
            <TaskCard
              task={s}
              projects={projects}
              actions={
                <>
                  <button
                    type="button"
                    className="task-action"
                    disabled={decidingId === s.id}
                    onClick={() => onDecide(s.id, 'approve')}
                  >
                    <CheckCircle2 size={14} aria-hidden="true" />
                    Approve
                  </button>
                  <button
                    type="button"
                    className="task-action danger-action"
                    disabled={decidingId === s.id}
                    onClick={() => onDecide(s.id, 'dismiss')}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                    Dismiss
                  </button>
                </>
              }
            />
          </li>
        ))}
      </ul>
    </div>
  )
}
