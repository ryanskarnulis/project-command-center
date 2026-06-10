import { useEffect, useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { TaskCard } from '../tasks/TaskCard'
import { useInbox } from './useInbox'

/** Shows a single pending note as a list of candidate TaskCards with Approve/Dismiss actions. */
function CandidateList({
  inboxId,
  candidates,
  projects,
  onDecide,
  submitting,
}: {
  inboxId: number
  candidates: Task[]
  projects: Project[]
  onDecide: (inboxId: number, taskId: number, action: 'approve' | 'dismiss') => void
  submitting: boolean
}) {
  if (candidates.length === 0) return <p>No remaining candidates.</p>
  return (
    <ul style={{ listStyle: 'none', padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {candidates.map((t) => (
        <li key={t.id}>
          <TaskCard
            task={t}
            projects={projects}
            actions={
              <>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => onDecide(inboxId, t.id, 'approve')}
                >
                  Approve
                </button>
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => onDecide(inboxId, t.id, 'dismiss')}
                >
                  Dismiss
                </button>
              </>
            }
          />
        </li>
      ))}
    </ul>
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

  // Review the riskiest extractions first: lowest model confidence at the top
  // (candidates without a confidence score sort last).
  const sortedCandidates = useMemo(
    () => [...candidates].sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1)),
    [candidates],
  )

  const suggestedProjectName =
    inboxItem === null
      ? null
      : (projects.find((p) => p.id === inboxItem.suggested_project_id)?.name ?? null)

  // /inbox/:inboxId opens that note's review directly (breadcrumb back-target);
  // plain /inbox returns to the list, clearing any previously-selected note.
  useEffect(() => {
    if (inboxId) {
      void selectItemById(Number(inboxId))
    } else {
      reset()
    }
  }, [inboxId, selectItemById, reset])

  function handleDecide(inboxId: number, taskId: number, action: 'approve' | 'dismiss') {
    void decide(inboxId, taskId, { action })
  }

  // Decide every remaining candidate at once via the batch review endpoint, which
  // finalizes the note and writes one training row.
  function handleDecideAll(action: 'accept' | 'reject') {
    void review(candidates.map((t) => ({ task_id: t.id, action })))
  }

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
          {candidates.length > 0 && (
            <p className="remaining-count">{candidates.length} remaining to review</p>
          )}
          {candidates.length > 0 && (
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
          )}
          <CandidateList
            inboxId={inboxItem.id}
            candidates={sortedCandidates}
            projects={projects}
            onDecide={handleDecide}
            submitting={submitting}
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
