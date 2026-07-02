import { CheckCircle2, Trash2 } from 'lucide-react'

interface CandidateDecisionBarProps {
  deciding: boolean
  onDecide: (action: 'approve' | 'dismiss') => void
}

/** Approve/dismiss actions for a task that is still an inbox-review candidate. */
export function CandidateDecisionBar({ deciding, onDecide }: CandidateDecisionBarProps) {
  return (
    <>
      <button type="button" disabled={deciding} onClick={() => onDecide('approve')}>
        <CheckCircle2 size={16} aria-hidden="true" />
        Approve
      </button>
      <button
        type="button"
        className="danger-action"
        disabled={deciding}
        onClick={() => onDecide('dismiss')}
      >
        <Trash2 size={16} aria-hidden="true" />
        Dismiss
      </button>
    </>
  )
}
