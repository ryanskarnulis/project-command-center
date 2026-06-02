import { useCallback, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  createInbox,
  dismissInbox,
  getCandidates,
  getInbox,
  listPendingInbox,
  processInbox,
  reviewInbox,
} from '../../api/inbox'
import { listProjects } from '../../api/projects'
import type { InboxItem, ReviewDecision } from '../../types/inbox'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'

interface UseInbox {
  inboxItem: InboxItem | null
  candidates: Task[]
  pending: InboxItem[]
  projects: Project[]
  loading: boolean
  error: string | null
  submitting: boolean
  notice: string | null
  submit: (rawText: string) => Promise<void>
  review: (decisions: ReviewDecision[]) => Promise<void>
  dismiss: (id: number) => Promise<void>
  loadPending: () => Promise<void>
  loadProjects: () => Promise<void>
  selectItem: (item: InboxItem) => Promise<void>
  reset: () => void
}

function messageFor(e: unknown, fallback: string): string {
  // A 422 from /process means extraction validation failed; the workflow already
  // logged the raw output and wrote a failure training row. Surface it, don't swallow it.
  if (e instanceof ApiError && e.status === 422) {
    return 'Extraction failed validation — the raw output was saved as a training example. Try editing the text and submitting again.'
  }
  // A 409 from /review means this note was already reviewed; re-reviewing is blocked
  // to keep the training data clean (one review per note).
  if (e instanceof ApiError && e.status === 409) {
    return 'This note was already reviewed — re-reviewing is disabled to keep training data clean.'
  }
  return e instanceof Error ? e.message : fallback
}

export function useInbox(): UseInbox {
  const [inboxItem, setInboxItem] = useState<InboxItem | null>(null)
  const [candidates, setCandidates] = useState<Task[]>([])
  const [pending, setPending] = useState<InboxItem[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const reset = useCallback(() => {
    setInboxItem(null)
    setCandidates([])
    setError(null)
    setNotice(null)
  }, [])

  // Projects populate the per-task project dropdown in the review queue.
  const loadProjects = useCallback(async () => {
    try {
      setProjects(await listProjects())
    } catch {
      // Non-fatal: accepted tasks still fall back to General on the backend.
    }
  }, [])

  // Items awaiting review: processed (have candidates) but not yet reviewed.
  // This is how out-of-band captures (e.g. Discord) surface in the web app.
  const loadPending = useCallback(async () => {
    try {
      setPending(await listPendingInbox())
    } catch (e: unknown) {
      setError(messageFor(e, 'Failed to load pending inbox items'))
    }
  }, [])

  // Open an existing pending item and load its candidates into the review queue.
  const selectItem = useCallback(async (item: InboxItem) => {
    setLoading(true)
    setError(null)
    setNotice(null)
    try {
      const tasks = await getCandidates(item.id)
      setInboxItem(item)
      setCandidates(tasks)
    } catch (e: unknown) {
      setError(messageFor(e, 'Failed to load candidates'))
    } finally {
      setLoading(false)
    }
  }, [])

  const submit = useCallback(async (rawText: string) => {
    setLoading(true)
    setError(null)
    setNotice(null)
    setCandidates([])
    try {
      const item = await createInbox({ raw_text: rawText })
      setInboxItem(item)
      const tasks = await processInbox(item.id)
      // Re-fetch: processing sets the matched-project suggestion on the item.
      setInboxItem(await getInbox(item.id))
      setCandidates(tasks)
    } catch (e: unknown) {
      setError(messageFor(e, 'Failed to process inbox text'))
    } finally {
      setLoading(false)
    }
  }, [])

  // Clear a pending item from the queue without reviewing it (soft-delete on the
  // backend). Its training examples are kept — they have no FK to the inbox row.
  const dismiss = useCallback(
    async (id: number) => {
      try {
        await dismissInbox(id)
        if (inboxItem?.id === id) reset()
        void loadPending()
      } catch (e: unknown) {
        setError(messageFor(e, 'Failed to dismiss inbox item'))
      }
    },
    [inboxItem, loadPending, reset],
  )

  const review = useCallback(
    async (decisions: ReviewDecision[]) => {
      if (inboxItem === null) return
      setSubmitting(true)
      setError(null)
      try {
        const res = await reviewInbox(inboxItem.id, { decisions })
        // Return to the main inbox screen with a transient confirmation; the
        // reviewed item drops out of the pending queue.
        setNotice(
          `Review saved — ${res.accepted} accepted, ${res.rejected} rejected.`,
        )
        setInboxItem(null)
        setCandidates([])
        void loadPending()
      } catch (e: unknown) {
        setError(messageFor(e, 'Failed to submit review'))
      } finally {
        setSubmitting(false)
      }
    },
    [inboxItem, loadPending],
  )

  return {
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
  }
}
