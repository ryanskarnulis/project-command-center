import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { CandidateDecision, ReviewDecision } from '../../types/inbox'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { CandidateCard } from './CandidateCard'
import { diffCandidateDraft, type CandidateDraft } from './candidateDraft'
import { useInbox } from './useInbox'

/**
 * The inline triage queue for one note: candidate cards editable in place,
 * decided one at a time (or all at once). Holds the per-candidate drafts, so
 * it's keyed by note id — switching notes remounts with clean state.
 */
function CandidateTriage({
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

export function InboxPage() {
  const { inboxId } = useParams<{ inboxId?: string }>()
  const navigate = useNavigate()
  const {
    inboxItem,
    candidates,
    pending,
    loading,
    error,
    submitting,
    notice,
    projects,
    decide,
    review,
    dismiss,
    loadPending,
    loadProjects,
    selectItemById,
    reset,
  } = useInbox()

  useEffect(() => {
    void loadPending()
    void loadProjects()
  }, [loadPending, loadProjects])

  const suggestedProjectName =
    inboxItem === null
      ? null
      : (projects.find((p) => p.id === inboxItem.suggested_project_id)?.name ?? null)

  // What the backend files an approved candidate under when no override is
  // sent: the note's matched project, else General. This is the diff baseline
  // for the project chip.
  const effectiveProjectId =
    inboxItem?.suggested_project_id ??
    projects.find((p) => p.system_key === 'general')?.id ??
    null

  // /inbox/:inboxId opens that note's review directly (breadcrumb back-target);
  // plain /inbox returns to the list, clearing any previously-selected note.
  useEffect(() => {
    if (inboxId) {
      void selectItemById(Number(inboxId))
    } else {
      reset()
    }
  }, [inboxId, selectItemById, reset])

  // Dismissing soft-deletes the whole note (no review, no training row), so guard
  // a misclick that would discard the candidates.
  function handleDismissNote(id: number) {
    if (window.confirm('Dismiss this note? Its candidates will not be reviewed.')) {
      void dismiss(id)
    }
  }

  return (
    <main>
      <h1>Inbox</h1>
      <p>Review extracted candidates below. Capture new notes from the dashboard.</p>

      {notice && (
        <p role="status">
          {notice} <Link to="/tasks">View filed tasks</Link>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {loading && <p>Loading…</p>}

      {inboxItem ? (
        <section>
          <p className="breadcrumb"><Link to="/inbox">← Inbox</Link></p>
          <h2>
            {inboxItem.summary ?? 'Note'}
            {' '}
            <button type="button" onClick={() => handleDismissNote(inboxItem.id)}>
              Dismiss note
            </button>
          </h2>
          <p className="source-pill">{inboxItem.raw_text}</p>
          {suggestedProjectName && (
            <p className="suggested-project">
              Suggested project: <strong>{suggestedProjectName}</strong>
            </p>
          )}
          <CandidateTriage
            key={inboxItem.id}
            candidates={candidates}
            projects={projects}
            effectiveProjectId={effectiveProjectId}
            submitting={submitting}
            onDecide={(taskId, decision) => void decide(inboxItem.id, taskId, decision)}
            onReviewAll={(decisions) => void review(decisions)}
          />
        </section>
      ) : (
        <>
          {!loading && pending.length === 0 && <p>No notes awaiting review.</p>}
          <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {pending.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="task-card"
                  style={{ width: '100%', textAlign: 'left', cursor: 'pointer' }}
                  onClick={() => navigate(`/inbox/${item.id}`)}
                >
                  <div className="task-card-body">
                    <span className="task-card-title">{item.summary ?? item.raw_text}</span>
                    <div className="task-card-badges">
                      <span className="source-pill">{item.source}</span>
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  )
}
