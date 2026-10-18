import type { ReactNode } from 'react'
import { RotateCcw } from 'lucide-react'
import { SwipeGestureRow } from '../../../components/SwipeGestureRow'

export { LONG_PRESS_MS, SWIPE_CLAMP_PX, SWIPE_COMMIT_PX } from '../../../hooks/useSwipeGesture'

interface TrashSwipeRowProps {
  /** Selection mode turns the gesture off: a checkbox row does not slide. */
  enabled: boolean
  onRestore: () => void
  /** A 500ms hold enters selection mode with this row checked. */
  onLongPress?: () => void
  children: ReactNode
}

/**
 * M08f's row gesture: drag right past 88px to restore. Left is deliberately
 * dead — the only thing it could mean on this route is purge, the one
 * irreversible write in the app, and undo cannot bring back a row that is
 * gone from the database. `Delete forever` stays behind ⋯.
 *
 * Mechanics are the shared `useSwipeGesture` (#322). The gesture is an
 * accelerator; the sheet is the route.
 */
export function TrashSwipeRow({ enabled, onRestore, onLongPress, children }: TrashSwipeRowProps) {
  return (
    <SwipeGestureRow
      classPrefix="trash-swipe"
      enabled={enabled}
      onSwipeRight={onRestore}
      hint="trash"
      onLongPress={onLongPress}
      renderReveal={({ right }) => (
        <div className="trash-swipe-reveal" aria-hidden="true" style={{ opacity: right }}>
          <RotateCcw size={15} />
          Restore
        </div>
      )}
    >
      {children}
    </SwipeGestureRow>
  )
}
