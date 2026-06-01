import { useState } from 'react'
import type { ReviewDecision, ReviewEdit } from '../../types/inbox'
import type { Project } from '../../types/project'
import type { Task, TaskPriority } from '../../types/task'

const PRIORITIES: TaskPriority[] = ['low', 'medium', 'high', 'urgent']

interface RowState {
  action: 'accept' | 'reject'
  title: string
  description: string
  due_date: string
  priority: TaskPriority
  assignee_hint: string
  projectId: number | null
}

interface ReviewQueueProps {
  candidates: Task[]
  projects: Project[]
  /** Project the note was matched to; rows default to it, overridable per task. */
  suggestedProjectId: number | null
  submitting: boolean
  onSubmitReview: (decisions: ReviewDecision[]) => void
}

/** Empty string in a form input maps back to a null field on the backend. */
function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed === '' ? null : trimmed
}

function initialRows(
  candidates: Task[],
  suggestedProjectId: number | null,
): Record<number, RowState> {
  const rows: Record<number, RowState> = {}
  for (const c of candidates) {
    rows[c.id] = {
      action: 'accept',
      title: c.title,
      description: c.description ?? '',
      due_date: c.due_date ?? '',
      priority: c.priority,
      assignee_hint: c.assignee_hint ?? '',
      projectId: c.project_id ?? suggestedProjectId,
    }
  }
  return rows
}

/** Build an edits object containing only fields the user changed from the candidate.
 *
 * The project baseline is the note's suggestion (what the backend applies when no
 * override is sent), not the candidate's own project_id, which is null pre-accept.
 */
function diffEdits(
  candidate: Task,
  row: RowState,
  suggestedProjectId: number | null,
): ReviewEdit | undefined {
  const edits: ReviewEdit = {}
  if (row.title.trim() !== candidate.title) edits.title = row.title.trim()
  if (orNull(row.description) !== candidate.description) {
    edits.description = orNull(row.description)
  }
  if (orNull(row.due_date) !== candidate.due_date) {
    edits.due_date = orNull(row.due_date)
  }
  if (row.priority !== candidate.priority) edits.priority = row.priority
  if (orNull(row.assignee_hint) !== candidate.assignee_hint) {
    edits.assignee_hint = orNull(row.assignee_hint)
  }
  if (row.projectId !== suggestedProjectId) edits.project_id = row.projectId
  return Object.keys(edits).length > 0 ? edits : undefined
}

export function ReviewQueue({
  candidates,
  projects,
  suggestedProjectId,
  submitting,
  onSubmitReview,
}: ReviewQueueProps) {
  const [rows, setRows] = useState<Record<number, RowState>>(() =>
    initialRows(candidates, suggestedProjectId),
  )

  function update(taskId: number, patch: Partial<RowState>) {
    setRows((prev) => ({ ...prev, [taskId]: { ...prev[taskId], ...patch } }))
  }

  function handleSubmit() {
    const decisions: ReviewDecision[] = candidates.map((c) => {
      const row = rows[c.id]
      if (row.action === 'reject') {
        return { task_id: c.id, action: 'reject' }
      }
      const edits = diffEdits(c, row, suggestedProjectId)
      return edits
        ? { task_id: c.id, action: 'accept', edits }
        : { task_id: c.id, action: 'accept' }
    })
    onSubmitReview(decisions)
  }

  return (
    <section>
      <h2>Review candidates ({candidates.length})</h2>
      <ul className="review-queue">
        {candidates.map((c) => {
          const row = rows[c.id]
          const rejected = row.action === 'reject'
          return (
            <li
              key={c.id}
              className={rejected ? 'review-row rejected' : 'review-row'}
            >
              <div className="review-row-controls">
                <label>
                  <input
                    type="checkbox"
                    checked={row.action === 'accept'}
                    onChange={(e) =>
                      update(c.id, {
                        action: e.target.checked ? 'accept' : 'reject',
                      })
                    }
                  />
                  {row.action === 'accept' ? 'Accept' : 'Reject'}
                </label>
                {c.confidence !== null && (
                  <span className="confidence">
                    confidence {c.confidence.toFixed(2)}
                  </span>
                )}
              </div>

              <input
                value={row.title}
                disabled={rejected}
                onChange={(e) => update(c.id, { title: e.target.value })}
                placeholder="Title"
              />
              <textarea
                value={row.description}
                disabled={rejected}
                onChange={(e) => update(c.id, { description: e.target.value })}
                placeholder="Description (optional)"
                rows={2}
              />
              <div className="review-row-meta">
                <input
                  type="date"
                  value={row.due_date}
                  disabled={rejected}
                  onChange={(e) => update(c.id, { due_date: e.target.value })}
                />
                <select
                  value={row.priority}
                  disabled={rejected}
                  onChange={(e) =>
                    update(c.id, { priority: e.target.value as TaskPriority })
                  }
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
                <input
                  value={row.assignee_hint}
                  disabled={rejected}
                  onChange={(e) =>
                    update(c.id, { assignee_hint: e.target.value })
                  }
                  placeholder="Assignee (optional)"
                />
                <select
                  aria-label="Project"
                  value={row.projectId === null ? '' : String(row.projectId)}
                  disabled={rejected}
                  onChange={(e) =>
                    update(c.id, {
                      projectId: e.target.value === '' ? null : Number(e.target.value),
                    })
                  }
                >
                  <option value="">— no project —</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>
            </li>
          )
        })}
      </ul>

      <button type="button" onClick={handleSubmit} disabled={submitting}>
        {submitting ? 'Submitting…' : 'Submit review'}
      </button>
    </section>
  )
}
