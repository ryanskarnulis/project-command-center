import type { ReactNode } from 'react'
import { Trash2 } from 'lucide-react'
import { SwipeGestureRow } from '../../../components/SwipeGestureRow'

export { LONG_PRESS_MS, SWIPE_CLAMP_PX, SWIPE_COMMIT_PX } from '../../../hooks/useSwipeGesture'

interface ConversationSwipeRowProps {
  /** Off while that conversation has a run in flight — the server 409s the
   * delete anyway (#149), so the row never offers the gesture it can't keep. */
  enabled: boolean
  onDelete: () => void
  /** A 500ms hold opens the row's actions sheet — the accessible path to the
   * same verbs, so the swipe is never the only route. */
  onLongPress: () => void
  children: ReactNode
}

/**
 * M07f's conversation row gesture: drag left past 88px to delete. Right is
 * dead — there is no second action on a conversation.
 *
 * Mechanics are the shared `useSwipeGesture` (#322). Delete acts immediately
 * and the page's undo bar is what makes it recoverable, which is why no
 * confirm stands between them.
 */
export function ConversationSwipeRow({ enabled, onDelete, onLongPress, children }: ConversationSwipeRowProps) {
  return (
    <SwipeGestureRow
      classPrefix="magent-swipe"
      enabled={enabled}
      onSwipeLeft={onDelete}
      hint="agent"
      onLongPress={onLongPress}
      renderReveal={({ left }) => (
        <div className="magent-swipe-reveal" aria-hidden="true" style={{ opacity: left }}>
          <Trash2 size={15} />
          Delete
        </div>
      )}
    >
      {children}
    </SwipeGestureRow>
  )
}
