import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Trash2 } from 'lucide-react'

/* The M05i gesture constants, restated here rather than imported from the
   focus or trash feature so the three routes stay independent (TODO.md has
   the note about extracting one primitive now that a third route wants it). */
export const SWIPE_COMMIT_PX = 88
export const SWIPE_CLAMP_PX = 132
export const LONG_PRESS_MS = 500

/* Movement past which the press is a drag rather than a tap. Below it the row
   does not move and the pointer stays uncaptured, so a plain tap still lands
   on whatever it was aimed at. */
const DRAG_SLOP_PX = 6

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
 * clamped to zero — there is no second action on a conversation.
 *
 * Mechanics are M05i's `SwipeRow`: capture is taken on the first move past the
 * slop, never on the press, because a captured pointer retargets the click
 * derived from it and would swallow every tap on the row's open button — the
 * accessible path. Delete acts immediately and the page's undo bar is what
 * makes it recoverable, which is why no confirm stands between them.
 */
export function ConversationSwipeRow({ enabled, onDelete, onLongPress, children }: ConversationSwipeRowProps) {
  const [dx, setDx] = useState(0)
  const [snapping, setSnapping] = useState(false)
  const origin = useRef<number | null>(null)
  // A drag or a long-press that ends on a control must not also activate it.
  const dragged = useRef(false)
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => { if (holdTimer.current) clearTimeout(holdTimer.current) }, [])

  function clearHold(): void {
    if (holdTimer.current) clearTimeout(holdTimer.current)
    holdTimer.current = null
  }

  function end(): void {
    clearHold()
    if (origin.current === null) return
    origin.current = null
    const committed = dx
    setSnapping(true)
    setDx(0)
    if (committed <= -SWIPE_COMMIT_PX) onDelete()
  }

  return (
    <div className="magent-swipe">
      {enabled && (
        <div
          className="magent-swipe-reveal"
          aria-hidden="true"
          style={{ opacity: Math.min(1, Math.max(0, -dx) / SWIPE_COMMIT_PX) }}
        >
          <Trash2 size={15} />
          Delete
        </div>
      )}
      <div
        className={snapping ? 'magent-swipe-content snapping' : 'magent-swipe-content'}
        style={{ transform: `translateX(${dx}px)` }}
        onTransitionEnd={() => setSnapping(false)}
        onPointerDown={(event) => {
          if (event.button !== 0) return
          dragged.current = false
          clearHold()
          holdTimer.current = setTimeout(() => {
            holdTimer.current = null
            origin.current = null
            // Swallow the click the release will produce, the way a drag does.
            dragged.current = true
            setDx(0)
            onLongPress()
          }, LONG_PRESS_MS)
          if (!enabled) return
          origin.current = event.clientX
          setSnapping(false)
        }}
        onPointerMove={(event) => {
          if (origin.current === null) return
          // Right is clamped to zero: the band never shows, the row never moves.
          const next = Math.min(0, event.clientX - origin.current)
          if (!dragged.current) {
            if (Math.abs(event.clientX - origin.current) <= DRAG_SLOP_PX) return
            clearHold()
            dragged.current = true
            // Capture is an optimisation — it keeps the drag alive past this
            // element's edge — and throws if the pointer is already gone.
            try {
              event.currentTarget.setPointerCapture(event.pointerId)
            } catch {
              /* Uncaptured: the drag ends early if the pointer leaves the row. */
            }
          }
          setDx(Math.max(-SWIPE_CLAMP_PX, next))
        }}
        onPointerUp={end}
        onPointerCancel={end}
        onClickCapture={(event) => {
          // `detail === 0` is a keyboard-activated click, which never followed
          // a drag; without the check a swipe would leave the row unreachable by
          // keyboard until it was touched again.
          if (!dragged.current || event.detail === 0) return
          event.preventDefault()
          event.stopPropagation()
          dragged.current = false
        }}
      >
        {children}
      </div>
    </div>
  )
}
