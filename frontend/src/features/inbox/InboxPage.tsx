import { type SubmitEvent, useEffect, useState } from 'react'
import { useInbox } from './useInbox'
import { ReviewQueue } from './ReviewQueue'

export function InboxPage() {
  const {
    inboxItem,
    candidates,
    pending,
    loading,
    error,
    submitting,
    notice,
    submit,
    review,
    loadPending,
    selectItem,
    reset,
  } = useInbox()
  const [text, setText] = useState('')

  // Surface items awaiting review (including out-of-band captures like Discord)
  // on load.
  useEffect(() => {
    void loadPending()
  }, [loadPending])

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!text.trim()) return
    await submit(text.trim())
  }

  function handleNewCapture() {
    setText('')
    reset()
    void loadPending()
  }

  // A re-pasted note resolves (idempotently) to an already-reviewed inbox item;
  // re-reviewing is blocked server-side, so show its state read-only instead.
  const alreadyReviewed = inboxItem?.reviewed_at != null
  const emptyCandidates =
    inboxItem != null && !loading && !alreadyReviewed && candidates.length === 0

  return (
    <main>
      <h1>Inbox</h1>

      {notice && <p role="status">{notice}</p>}

      <form onSubmit={handleSubmit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste messy notes here — meetings, todos, anything…"
          rows={6}
        />
        <button type="submit" disabled={loading || !text.trim()}>
          {loading ? 'Processing…' : 'Extract tasks'}
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      {pending.length > 0 && (
        <section>
          <h2>Awaiting review ({pending.length})</h2>
          <ul>
            {pending.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  disabled={loading}
                  aria-current={inboxItem?.id === item.id}
                  onClick={() => void selectItem(item)}
                >
                  <span>[{item.source}]</span>{' '}
                  {item.summary ?? item.raw_text.slice(0, 60)}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {inboxItem && (
        <section>
          {inboxItem.summary && (
            <p>
              <strong>Summary:</strong> {inboxItem.summary}
            </p>
          )}
          {inboxItem.project_hint && (
            <p>
              <strong>Project hint:</strong> {inboxItem.project_hint}
            </p>
          )}
          {inboxItem.needs_review && <p>Flagged for review.</p>}
        </section>
      )}

      {!alreadyReviewed && candidates.length > 0 && (
        <ReviewQueue
          key={inboxItem?.id}
          candidates={candidates}
          submitting={submitting}
          onSubmitReview={(decisions) => void review(decisions)}
        />
      )}

      {emptyCandidates && (
        <section>
          <p>No tasks were extracted from this note.</p>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void review([])}
          >
            {submitting ? 'Dismissing…' : 'Dismiss (no tasks)'}
          </button>
        </section>
      )}

      {alreadyReviewed && (
        <section>
          <p>This note was already reviewed — re-reviewing is disabled.</p>
          <button type="button" onClick={handleNewCapture}>
            New capture
          </button>
        </section>
      )}
    </main>
  )
}
