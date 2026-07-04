import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { CandidateTriage } from './CandidateTriage'
import { useInbox } from './useInbox'

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
              {inboxItem.matched_alias && (
                <span className="matched-alias">
                  {' '}· matched alias “{inboxItem.matched_alias}”
                </span>
              )}
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
