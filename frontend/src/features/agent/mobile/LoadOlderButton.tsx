import { ChevronDown, ChevronUp } from 'lucide-react'

interface LoadOlderButtonProps {
  /** The verb phrase, e.g. "Load older messages" — also the accessible name. */
  label: string
  loading: boolean
  /** Where the older page lands: above the button (the thread) or below it
   * (the conversations sheet). Only the chevron changes. */
  direction: 'up' | 'down'
  onLoad: () => void
}

/* #325 — the mobile Agent's two paging controls (older messages at the top of
 * the thread, older conversations at the foot of the sheet) share one quiet
 * shape: a hairline divider with the action word set into it. It reads as
 * "there is more past this line" rather than as a third button competing with
 * the composer or the sheet's rows, and the full-width row keeps the 44px
 * target while the ink stays at 12px.
 *
 * While a page is loading the control is `aria-disabled`, not `disabled`: a
 * disabled button drops keyboard focus to <body> mid-load, so a keyboard user
 * paging back through a long thread would have to tab back to it every time.
 * The click is ignored instead. */
export function LoadOlderButton({ label, loading, direction, onLoad }: LoadOlderButtonProps) {
  const Chevron = direction === 'up' ? ChevronUp : ChevronDown
  return (
    <button
      type="button"
      className="magent-older"
      aria-disabled={loading || undefined}
      aria-busy={loading || undefined}
      onClick={() => {
        if (!loading) onLoad()
      }}
    >
      <span className="magent-older-label">
        <Chevron size={14} aria-hidden="true" />
        {loading ? 'Loading…' : label}
      </span>
    </button>
  )
}
