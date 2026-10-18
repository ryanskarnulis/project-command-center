import type { ReactNode } from 'react'
import { Check, CornerDownRight } from 'lucide-react'
import { SwipeGestureRow } from '../../../components/SwipeGestureRow'

interface SwipeRowProps {
  /** Finished rows are inert; the gesture belongs to work you can still act on. */
  enabled: boolean
  onDone: () => void
  onDefer: () => void
  children: ReactNode
}

/**
 * The M05i row gesture: drag right past 88px to mark done, left to defer.
 * Mechanics — slop, late capture, click swallowing — are the shared
 * `useSwipeGesture` (#322).
 *
 * Only this cell translates — the gutter's clock time and spine stay pinned,
 * because they are the scale the day is measured against.
 *
 * The gesture is an accelerator, never the only route: both verbs also live in
 * the row's ⋯ sheet, which is what a keyboard or screen reader uses.
 */
export function SwipeRow({ enabled, onDone, onDefer, children }: SwipeRowProps) {
  return (
    <SwipeGestureRow
      classPrefix="focus-swipe"
      enabled={enabled}
      onSwipeRight={onDone}
      onSwipeLeft={onDefer}
      hint="focus"
      renderReveal={({ right, left }) => (
        <div className="focus-swipe-reveal" aria-hidden="true">
          <span className="focus-reveal-done" style={{ opacity: right }}>
            <Check size={16} />
            Done
          </span>
          <span className="focus-reveal-defer" style={{ opacity: left }}>
            Defer
            <CornerDownRight size={16} />
          </span>
        </div>
      )}
    >
      {children}
    </SwipeGestureRow>
  )
}
