import { useEffect, useRef, useState, type ReactNode } from 'react'
import { RotateCcw } from 'lucide-react'

/* The M05i gesture constants, restated here rather than imported from the
   focus feature so the two routes stay independent. */
export const SWIPE_COMMIT_PX = 88
export const SWIPE_CLAMP_PX = 132
export const LONG_PRESS_MS = 500

/* Movement past which the press is a drag rather than a tap. Below it the row
   does not move and the pointer stays uncaptured, so a plain tap still lands
   on whatever it was aimed at. */
const DRAG_SLOP_PX = 6

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
 * Mechanics are M05i's `SwipeRow`: capture is taken on the first move past the
 * slop, never on the press, because a captured pointer retargets the click
 * derived from it and would swallow every tap on the ring and the ⋯ — the
 * accessible path. The gesture is an accelerator; the sheet is the route.
 */
export function TrashSwipeRow({ enabled, onRestore, onLongPress, children }: TrashSwipeRowProps) {
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
    if (committed >= SWIPE_COMMIT_PX) onRestore()
  }

  return (
    <div className="trash-swipe">
      {enabled && (
        <div className="trash-swipe-reveal" aria-hidden="true" style={{ opacity: Math.min(1, Math.max(0, dx) / SWIPE_COMMIT_PX) }}>
          <RotateCcw size={15} />
          Restore
        </div>
      )}
      <div
        className={snapping ? 'trash-swipe-content snapping' : 'trash-swipe-content'}
        style={{ transform: `translateX(${dx}px)` }}
        onTransitionEnd={() => setSnapping(false)}
        onPointerDown={(event) => {
          if (event.button !== 0) return
          dragged.current = false
          if (onLongPress) {
            clearHold()
            holdTimer.current = setTimeout(() => {
              holdTimer.current = null
              origin.current = null
              // Swallow the click the release will produce, the way a drag does.
              dragged.current = true
              setDx(0)
              onLongPress()
            }, LONG_PRESS_MS)
          }
          if (!enabled) return
          origin.current = event.clientX
          setSnapping(false)
        }}
        onPointerMove={(event) => {
          if (origin.current === null) return
          // Left is clamped to zero: the band never shows, the row never moves.
          const next = Math.max(0, event.clientX - origin.current)
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
          setDx(Math.min(SWIPE_CLAMP_PX, next))
        }}
        onPointerUp={end}
        onPointerCancel={end}
        onClickCapture={(event) => {
          // `detail === 0` is a keyboard-activated click, which never followed
          // a drag; without the check a swipe would leave the ring and ⋯
          // unreachable by keyboard until the row was touched again.
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
