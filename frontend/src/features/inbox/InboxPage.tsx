import { type SubmitEvent, useState } from 'react'
import { useInbox } from './useInbox'
import { ReviewQueue } from './ReviewQueue'

export function InboxPage() {
  const {
    inboxItem,
    candidates,
    loading,
    error,
    submitting,
    result,
    submit,
    review,
    reset,
  } = useInbox()
  const [text, setText] = useState('')

  async function handleSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!text.trim()) return
    await submit(text.trim())
  }

  function handleNewCapture() {
    setText('')
    reset()
  }

  const reviewed = result !== null
  // A re-pasted note resolves (idempotently) to an already-reviewed inbox item;
  // re-reviewing is blocked server-side, so show its state read-only instead.
  const alreadyReviewed = inboxItem?.reviewed_at != null

  return (
    <main>
      <h1>Inbox</h1>

      {!reviewed && (
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
      )}

      {error && <p role="alert">{error}</p>}

      {inboxItem && !reviewed && (
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

      {!reviewed && !alreadyReviewed && candidates.length > 0 && (
        <ReviewQueue
          key={inboxItem?.id}
          candidates={candidates}
          submitting={submitting}
          onSubmitReview={(decisions) => void review(decisions)}
        />
      )}

      {!reviewed && alreadyReviewed && (
        <section>
          <p>This note was already reviewed — re-reviewing is disabled.</p>
          <button type="button" onClick={handleNewCapture}>
            New capture
          </button>
        </section>
      )}

      {inboxItem &&
        !loading &&
        !error &&
        !reviewed &&
        candidates.length === 0 && <p>No task candidates were extracted.</p>}

      {reviewed && result && (
        <section>
          <p>
            Review saved — {result.accepted} accepted, {result.rejected}{' '}
            rejected. Training example #{result.training_example_id} recorded.
          </p>
          <button type="button" onClick={handleNewCapture}>
            New capture
          </button>
        </section>
      )}
    </main>
  )
}
