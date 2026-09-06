import { useRef, useState, type ReactNode } from 'react'
import { Check, CornerDownRight } from 'lucide-react'
import { SWIPE_CLAMP_PX, SWIPE_COMMIT_PX } from './focusDay'

/* Movement past which the press is a drag rather than a tap. Below it the row
   does not move and the pointer stays uncaptured, so a plain tap still lands
   on whatever it was aimed at. */
const DRAG_SLOP_PX = 6

interface SwipeRowProps {
  /** Finished rows are inert; the gesture belongs to work you can still act on. */
  enabled: boolean
  onDone: () => void
  onDefer: () => void
  children: ReactNode
}

/**
 * The M05i row gesture: drag right past 88px to mark done, left to defer.
 *
 * Only this cell translates — the gutter's clock time and spine stay pinned,
 * because they are the scale the day is measured against. `touch-action: pan-y`
 * leaves vertical scrolling to the browser.
 *
 * Capture is taken on the first move past the slop, never on the press. A
 * captured pointer retargets its own `pointerup` *and the click derived from
 * it* to the capturing element, so capturing up front silently swallowed every
 * tap on the row's ⋯, the clock chip and the title link — the whole accessible
 * path — leaving the gesture as the only way to act on a block. Capturing only
 * once the row is actually moving still keeps the drag alive past the
 * element's edge, which is the reason to capture at all.
 *
 * The gesture is an accelerator, never the only route: both verbs also live in
 * the row's ⋯ sheet, which is what a keyboard or screen reader uses.
 */
export function SwipeRow({ enabled, onDone, onDefer, children }: SwipeRowProps) {
  const [dx, setDx] = useState(0)
  const [snapping, setSnapping] = useState(false)
  const origin = useRef<number | null>(null)
  // A drag that ends on the title must not also follow its link.
  const dragged = useRef(false)

  function end(): void {
    if (origin.current === null) return
    origin.current = null
    const committed = dx
    setSnapping(true)
    setDx(0)
    if (committed >= SWIPE_COMMIT_PX) onDone()
    else if (committed <= -SWIPE_COMMIT_PX) onDefer()
  }

  return (
    <div className="focus-swipe">
      {enabled && (
        <div className="focus-swipe-reveal" aria-hidden="true">
          <span className="focus-reveal-done" style={{ opacity: dx > 0 ? Math.min(1, dx / SWIPE_COMMIT_PX) : 0 }}>
            <Check size={16} />
            Done
          </span>
          <span className="focus-reveal-defer" style={{ opacity: dx < 0 ? Math.min(1, -dx / SWIPE_COMMIT_PX) : 0 }}>
            Defer
            <CornerDownRight size={16} />
          </span>
        </div>
      )}
      <div
        className={snapping ? 'focus-swipe-content snapping' : 'focus-swipe-content'}
        style={{ transform: `translateX(${dx}px)` }}
        onTransitionEnd={() => setSnapping(false)}
        onPointerDown={(event) => {
          if (!enabled || event.button !== 0) return
          origin.current = event.clientX
          dragged.current = false
          setSnapping(false)
        }}
        onPointerMove={(event) => {
          if (origin.current === null) return
          const next = event.clientX - origin.current
          if (!dragged.current) {
            if (Math.abs(next) <= DRAG_SLOP_PX) return
            dragged.current = true
            // Capture is an optimisation — it keeps the drag alive past this
            // element's edge. It throws NotFoundError if the pointer is already
            // gone, and an exception escaping here would kill the gesture
            // mid-drag, so the row still follows the pointer without it.
            try {
              event.currentTarget.setPointerCapture(event.pointerId)
            } catch {
              /* Uncaptured: the drag ends early if the pointer leaves the row. */
            }
          }
          setDx(Math.max(-SWIPE_CLAMP_PX, Math.min(SWIPE_CLAMP_PX, next)))
        }}
        onPointerUp={end}
        onPointerCancel={end}
        onClickCapture={(event) => {
          // `detail === 0` is a keyboard-activated click, which by definition
          // never followed a drag. `dragged` is only cleared by the next
          // pointerdown, so without this check a swipe would leave the row's ⋯
          // and title unreachable by keyboard until someone touched the row
          // again — the accessible path, broken by the accelerator.
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
