import { useEffect, useRef } from 'react'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { AssigneeChip } from '../tasks/chips/AssigneeChip'
import { DueDateChip } from '../tasks/chips/DueDateChip'
import { PriorityChip } from '../tasks/chips/PriorityChip'
import { ProjectChip } from '../tasks/chips/ProjectChip'
import type { CandidateDraft } from './candidateDraft'

interface Props {
  candidate: Task
  projects: Project[]
  /** What the backend files the task under if no project override is sent. */
  effectiveProjectId: number | null
  draft: CandidateDraft
  onDraftChange: (patch: CandidateDraft) => void
  onApprove: () => void
  onDismiss: () => void
  submitting: boolean
  /** Focus the title input — set on the next card after a decision. */
  autoFocus?: boolean
}

/**
 * An inbox candidate, editable in place: title/description as inputs, the
 * rest as the shared slice-1 chips. Draft fields are overrides — `undefined`
 * falls through to the candidate's own value (see `candidateDraft.ts`).
 */
export function CandidateCard({
  candidate,
  projects,
  effectiveProjectId,
  draft,
  onDraftChange,
  onApprove,
  onDismiss,
  submitting,
  autoFocus,
}: Props) {
  const titleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (autoFocus) titleRef.current?.focus()
  }, [autoFocus])

  const title = draft.title ?? candidate.title
  const description = draft.description !== undefined ? draft.description : candidate.description
  const dueDate = draft.due_date !== undefined ? draft.due_date : candidate.due_date
  const priority = draft.priority ?? candidate.priority
  const assignee =
    draft.assignee_hint !== undefined ? draft.assignee_hint : candidate.assignee_hint
  const projectId =
    draft.project_id !== undefined
      ? draft.project_id
      : candidate.project_id ?? effectiveProjectId

  return (
    <div className="candidate-card">
      <div className="candidate-card-controls">
        {candidate.confidence !== null && (
          <span className="confidence">confidence {candidate.confidence.toFixed(2)}</span>
        )}
        <div className="candidate-card-actions">
          <button
            type="button"
            disabled={submitting || title.trim() === ''}
            onClick={onApprove}
          >
            Approve
          </button>
          <button
            type="button"
            className="secondary-action"
            disabled={submitting}
            onClick={onDismiss}
          >
            Dismiss
          </button>
        </div>
      </div>

      <input
        ref={titleRef}
        aria-label={`Title for ${candidate.title}`}
        value={title}
        onChange={(e) => onDraftChange({ title: e.target.value })}
        placeholder="Title"
      />
      <textarea
        aria-label={`Description for ${candidate.title}`}
        value={description ?? ''}
        onChange={(e) => onDraftChange({ description: e.target.value })}
        placeholder="Description (optional)"
        rows={2}
      />
      <div className="candidate-card-chips">
        <PriorityChip
          value={priority}
          onChange={(next) => onDraftChange({ priority: next })}
        />
        <DueDateChip
          value={dueDate}
          onChange={(next) => onDraftChange({ due_date: next })}
        />
        <ProjectChip
          value={projectId}
          projects={projects}
          onChange={(next) => onDraftChange({ project_id: next })}
        />
        <AssigneeChip
          value={assignee}
          onChange={(next) => onDraftChange({ assignee_hint: next })}
        />
      </div>
    </div>
  )
}
