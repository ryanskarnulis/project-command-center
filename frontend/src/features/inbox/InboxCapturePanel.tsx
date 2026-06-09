import { type FormEvent, useEffect, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { useInbox } from './useInbox'
import { ReviewQueue } from './ReviewQueue'

interface InboxCapturePanelProps {
  title?: string
  description?: string
  className?: string
  headingLevel?: 1 | 2
  onPendingCountChange?: (count: number) => void
}

export function InboxCapturePanel({
  title = 'Inbox',
  description,
  className = '',
  headingLevel = 1,
  onPendingCountChange,
}: InboxCapturePanelProps) {
  const {
    inboxItem,
    candidates,
    pending,
    projects,
    loading,
    error,
    submitting,
    notice,
    submit,
    review,
    dismiss,
    loadPending,
    loadProjects,
    selectItem,
    reset,
  } = useInbox()
  const [text, setText] = useState('')

  // Surface items awaiting review (including out-of-band captures like Discord)
  // and load projects for the review-queue project picker on load.
  useEffect(() => {
    void loadPending()
    void loadProjects()
  }, [loadPending, loadProjects])

  useEffect(() => {
    onPendingCountChange?.(pending.length)
  }, [onPendingCountChange, pending.length])

  const suggestedProject =
    inboxItem === null
      ? null
      : (projects.find((p) => p.id === inboxItem.suggested_project_id) ?? null)
  const generalProject = projects.find((p) => p.system_key === 'general') ?? null

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!text.trim()) return
    await submit(text.trim())
  }

  function handleNewCapture() {
    setText('')
    reset()
    void loadPending()
  }

  async function handleReview(decisions: Parameters<typeof review>[0]) {
    await review(decisions)
    setText('')
  }

  const alreadyReviewed = inboxItem?.reviewed_at != null
  const emptyCandidates =
    inboxItem != null && !loading && !alreadyReviewed && candidates.length === 0
  const Heading = headingLevel === 1 ? 'h1' : 'h2'

  return (
    <section className={`inbox-capture-panel ${className}`.trim()}>
      <div className="inbox-capture-header">
        <div>
          <Heading>{title}</Heading>
          {description && <p>{description}</p>}
        </div>
      </div>

      {notice && <p role="status">{notice}</p>}

      <form onSubmit={handleSubmit} className="inbox-capture-form">
        <textarea
          aria-label="Messy text for AI task extraction"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste messy notes here - meetings, todos, anything..."
          rows={6}
        />
        <button
          type="submit"
          className="inbox-submit-button"
          disabled={loading || !text.trim()}
          aria-label={loading ? 'Processing' : 'Extract tasks'}
        >
          <ArrowUp size={18} aria-hidden="true" />
        </button>
      </form>

      {error && <p role="alert">{error}</p>}

      {inboxItem && (
        <section className="capture-summary-panel">
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
          {suggestedProject && (
            <p>
              <strong>Matched project:</strong> {suggestedProject.name}
            </p>
          )}
          {inboxItem.needs_review && <p>Flagged for review.</p>}
        </section>
      )}

      {!alreadyReviewed && candidates.length > 0 && (
        <ReviewQueue
          key={`${inboxItem?.id}:${suggestedProject?.id ?? ''}:${
            generalProject?.id ?? ''
          }`}
          candidates={candidates}
          projects={projects}
          suggestedProjectId={suggestedProject?.id ?? null}
          defaultProjectId={generalProject?.id ?? null}
          submitting={submitting}
          onSubmitReview={(decisions) => void handleReview(decisions)}
        />
      )}

      {emptyCandidates && (
        <section className="capture-summary-panel">
          <p>No tasks were extracted from this note.</p>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void handleReview([])}
          >
            {submitting ? 'Dismissing...' : 'Dismiss (no tasks)'}
          </button>
        </section>
      )}

      {alreadyReviewed && (
        <section className="capture-summary-panel">
          <p>This note was already reviewed - re-reviewing is disabled.</p>
          <button type="button" onClick={handleNewCapture}>
            New capture
          </button>
        </section>
      )}
    </section>
  )
}
