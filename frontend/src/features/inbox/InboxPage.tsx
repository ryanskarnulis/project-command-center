import { useEffect } from 'react'
import type { Task } from '../../types/task'
import { TaskCard } from '../tasks/TaskCard'
import { useInbox } from './useInbox'

/** Shows a single pending note as a list of candidate TaskCards with Approve/Dismiss actions. */
function CandidateList({
  inboxId,
  candidates,
  onDecide,
  submitting,
}: {
  inboxId: number
  candidates: Task[]
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
  const {
    inboxItem,
    candidates,
    pending,
    loading,
    error,
    submitting,
    notice,
    decide,
    dismiss,
    loadPending,
    selectItem,
  } = useInbox()

  useEffect(() => {
    void loadPending()
  }, [loadPending])

  function handleDecide(inboxId: number, taskId: number, action: 'approve' | 'dismiss') {
    void decide(inboxId, taskId, { action })
  }

  return (
    <main>
      <h1>Inbox</h1>
      <p>Review extracted candidates below. Capture new notes from the dashboard.</p>

      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
      {loading && <p>Loading…</p>}

      {inboxItem ? (
        <section>
          <h2>
            {inboxItem.summary ?? 'Note'}
            {' '}
            <button type="button" onClick={() => void dismiss(inboxItem.id)}>
              Dismiss note
            </button>
          </h2>
          <p className="source-pill">{inboxItem.raw_text}</p>
          <CandidateList
            inboxId={inboxItem.id}
            candidates={candidates}
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
                  onClick={() => void selectItem(item)}
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
