import { useMemo, useState } from 'react'
import type { CandidateDecision, ReviewDecision } from '../../types/inbox'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { CandidateCard } from './CandidateCard'
import { diffCandidateDraft, type CandidateDraft } from './candidateDraft'

/**
 * The inline triage queue for one note: candidate cards editable in place,
 * decided one at a time (or all at once). Holds the per-candidate drafts, so
 * it's keyed by note id — switching notes remounts with clean state.
 */
export function CandidateTriage({
  candidates,
  projects,
  effectiveProjectId,
  submitting,
  onDecide,
  onReviewAll,
}: {
  candidates: Task[]
  projects: Project[]
  effectiveProjectId: number | null
  submitting: boolean
  onDecide: (taskId: number, decision: CandidateDecision) => void
  onReviewAll: (decisions: ReviewDecision[]) => void
}) {
  // In-place edits per candidate, applied with the approve decision so the
  // training row records the correction.
  const [drafts, setDrafts] = useState<Record<number, CandidateDraft>>({})
  // The card to focus after a decision — triage auto-advances down the queue.
  const [focusTaskId, setFocusTaskId] = useState<number | null>(null)

  // Review the riskiest extractions first: lowest model confidence at the top
  // (candidates without a confidence score sort last).
  const sorted = useMemo(
    () => [...candidates].sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1)),
    [candidates],
  )

  function updateDraft(taskId: number, patch: CandidateDraft) {
    setDrafts((prev) => ({ ...prev, [taskId]: { ...prev[taskId], ...patch } }))
  }

  // Approve applies the card's in-place edits with the decision (one call, one
  // correction captured). Afterwards focus advances to the next candidate.
  function handleDecide(task: Task, action: 'approve' | 'dismiss') {
    const edits =
      action === 'approve'
        ? diffCandidateDraft(task, drafts[task.id] ?? {}, effectiveProjectId)
        : undefined
    const idx = sorted.findIndex((t) => t.id === task.id)
    const next = sorted.slice(idx + 1)[0] ?? sorted.find((t) => t.id !== task.id)
    onDecide(task.id, edits ? { action, edits } : { action })
    setFocusTaskId(next?.id ?? null)
  }

  // Decide every remaining candidate at once via the batch review endpoint,
  // which finalizes the note and writes one training row. Accepts carry any
  // in-place edits, same as a per-card approve.
  function handleDecideAll(action: 'accept' | 'reject') {
    onReviewAll(
      candidates.map((t) => {
        if (action === 'reject') return { task_id: t.id, action }
        const edits = diffCandidateDraft(t, drafts[t.id] ?? {}, effectiveProjectId)
        return edits ? { task_id: t.id, action, edits } : { task_id: t.id, action }
      }),
    )
  }

  if (candidates.length === 0) return <p>No remaining candidates.</p>
  return (
    <>
      <p className="remaining-count">{candidates.length} remaining to review</p>
      <div className="bulk-actions">
        <button
          type="button"
          disabled={submitting}
          onClick={() => handleDecideAll('accept')}
        >
          Approve all
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => handleDecideAll('reject')}
        >
          Dismiss all
        </button>
      </div>
      <ul className="candidate-list">
        {sorted.map((t) => (
          <li key={t.id}>
            <CandidateCard
              candidate={t}
              projects={projects}
              effectiveProjectId={effectiveProjectId}
              draft={drafts[t.id] ?? {}}
              onDraftChange={(patch) => updateDraft(t.id, patch)}
              onApprove={() => handleDecide(t, 'approve')}
              onDismiss={() => handleDecide(t, 'dismiss')}
              submitting={submitting}
              autoFocus={t.id === focusTaskId}
            />
          </li>
        ))}
      </ul>
    </>
  )
}
