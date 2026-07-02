import { type FormEvent, useEffect, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { Button } from '../../components/Button'
import type { CandidateDecision } from '../../types/inbox'
import { useInbox } from './useInbox'
import { CandidateTriage } from './CandidateTriage'

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
    decide,
    loadPending,
    loadProjects,
    reset,
  } = useInbox()
  const [text, setText] = useState('')
  // Per-card decisions leave inboxItem set with an empty candidate list once the
  // note finalizes — track it so that end state isn't mistaken for an empty
  // extraction ("No tasks were extracted").
  const [finalized, setFinalized] = useState(false)

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
    setFinalized(false)
    await submit(text.trim())
  }

  function handleNewCapture() {
    setText('')
    setFinalized(false)
    reset()
    void loadPending()
  }

  async function handleReview(decisions: Parameters<typeof review>[0]) {
    await review(decisions)
    setText('')
  }

  async function handleDecide(taskId: number, decision: CandidateDecision) {
    if (inboxItem === null) return
    const res = await decide(inboxItem.id, taskId, decision)
    if (res?.finalized) setFinalized(true)
  }

  const alreadyReviewed = inboxItem?.reviewed_at != null
  const emptyCandidates =
    inboxItem != null &&
    !loading &&
    !alreadyReviewed &&
    !finalized &&
    candidates.length === 0
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

      {!alreadyReviewed && candidates.length > 0 && inboxItem && (
        <CandidateTriage
          key={inboxItem.id}
          candidates={candidates}
          projects={projects}
          effectiveProjectId={suggestedProject?.id ?? generalProject?.id ?? null}
          submitting={submitting}
          onDecide={(taskId, decision) => void handleDecide(taskId, decision)}
          onReviewAll={(decisions) => void handleReview(decisions)}
        />
      )}

      {finalized && candidates.length === 0 && (
        <section className="capture-summary-panel">
          <Button onClick={handleNewCapture}>New capture</Button>
        </section>
      )}

      {emptyCandidates && (
        <section className="capture-summary-panel">
          <p>No tasks were extracted from this note.</p>
          <Button
            disabled={submitting}
            onClick={() => void handleReview([])}
          >
            {submitting ? 'Dismissing...' : 'Dismiss (no tasks)'}
          </Button>
        </section>
      )}

      {alreadyReviewed && (
        <section className="capture-summary-panel">
          <p>This note was already reviewed - re-reviewing is disabled.</p>
          <Button onClick={handleNewCapture}>New capture</Button>
        </section>
      )}
    </section>
  )
}
