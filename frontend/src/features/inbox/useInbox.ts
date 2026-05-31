import { useCallback, useState } from 'react'
import { ApiError } from '../../api/client'
import { createInbox, processInbox, reviewInbox } from '../../api/inbox'
import type {
  InboxItem,
  ReviewDecision,
  ReviewResult,
} from '../../types/inbox'
import type { Task } from '../../types/task'

interface UseInbox {
  inboxItem: InboxItem | null
  candidates: Task[]
  loading: boolean
  error: string | null
  submitting: boolean
  result: ReviewResult | null
  submit: (rawText: string) => Promise<void>
  review: (decisions: ReviewDecision[]) => Promise<void>
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
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<ReviewResult | null>(null)

  const reset = useCallback(() => {
    setInboxItem(null)
    setCandidates([])
    setError(null)
    setResult(null)
  }, [])

  const submit = useCallback(async (rawText: string) => {
    setLoading(true)
    setError(null)
    setResult(null)
    setCandidates([])
    try {
      const item = await createInbox({ raw_text: rawText })
      setInboxItem(item)
      const tasks = await processInbox(item.id)
      setCandidates(tasks)
    } catch (e: unknown) {
      setError(messageFor(e, 'Failed to process inbox text'))
    } finally {
      setLoading(false)
    }
  }, [])

  const review = useCallback(
    async (decisions: ReviewDecision[]) => {
      if (inboxItem === null) return
      setSubmitting(true)
      setError(null)
      try {
        const res = await reviewInbox(inboxItem.id, { decisions })
        setResult(res)
      } catch (e: unknown) {
        setError(messageFor(e, 'Failed to submit review'))
      } finally {
        setSubmitting(false)
      }
    },
    [inboxItem],
  )

  return {
    inboxItem,
    candidates,
    loading,
    error,
    submitting,
    result,
    submit,
    review,
    reset,
  }
}
