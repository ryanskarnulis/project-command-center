import { useEffect, useRef, useState, type MouseEvent, type PointerEvent } from 'react'

/* The M05i row-gesture constants — the one home for every swipe row (#322). */
/** Past this the gesture commits; below it the row snaps back. */
export const SWIPE_COMMIT_PX = 88
/** The drag cannot pull the content further than this. */
export const SWIPE_CLAMP_PX = 132
/** How long a still press must be held to count as a long-press. */
export const LONG_PRESS_MS = 500
/* Movement past which the press is a drag rather than a tap. Below it the row
   does not move and the pointer stays uncaptured, so a plain tap still lands
   on whatever it was aimed at. */
const DRAG_SLOP_PX = 6

export interface SwipeGestureOptions {
  /** Off: the content never slides and no side commits. A long-press still
   * fires — it is the row's route to its sheet, not part of the slide. */
  enabled: boolean
  /** Commit for a drag right past `SWIPE_COMMIT_PX`. Omitted: right is dead —
   * clamped to zero, the band never shows, the row never moves. */
  onSwipeRight?: () => void
  /** Commit for a drag left past `SWIPE_COMMIT_PX`. Omitted: left is dead. */
  onSwipeLeft?: () => void
  /** A `LONG_PRESS_MS` hold without moving past the slop. */
  onLongPress?: () => void
}

export interface SwipeGestureHandlers {
  onPointerDown: (event: PointerEvent<HTMLElement>) => void
  onPointerMove: (event: PointerEvent<HTMLElement>) => void
  onPointerUp: () => void
  onPointerCancel: () => void
  onClickCapture: (event: MouseEvent<HTMLElement>) => void
  onTransitionEnd: () => void
}

export interface SwipeGesture {
  /** Current translateX of the content, already clamped per side. */
  dx: number
  /** True while the content animates back to rest after a release. */
  snapping: boolean
  /** Spread onto the element that translates. */
  handlers: SwipeGestureHandlers
}

/**
 * The shared row gesture: drag the content sideways past `SWIPE_COMMIT_PX` to
 * commit that side's verb; release short of it and the row snaps back.
 *
 * Capture is taken on the first move past the slop, never on the press. A
 * captured pointer retargets its own `pointerup` *and the click derived from
 * it* to the capturing element, so capturing up front silently swallowed every
 * tap on the row's controls — the whole accessible path — leaving the gesture
 * as the only way to act on a row. Capturing only once the row is actually
 * moving still keeps the drag alive past the element's edge, which is the
 * reason to capture at all.
 *
 * The gesture is an accelerator, never the only route: every row using it must
 * also offer its verbs somewhere a keyboard or screen reader can reach.
 *
 * #320 seam: a first-run discoverability hint belongs here, not in a row — it
 * would play a short peek through this hook's `dx`/`snapping` (the same
 * transform and snap-back the real drag uses), so every swipe row gets it.
 */
export function useSwipeGesture({ enabled, onSwipeRight, onSwipeLeft, onLongPress }: SwipeGestureOptions): SwipeGesture {
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
    if (onSwipeRight && committed >= SWIPE_COMMIT_PX) onSwipeRight()
    else if (onSwipeLeft && committed <= -SWIPE_COMMIT_PX) onSwipeLeft()
  }

  const handlers: SwipeGestureHandlers = {
    onTransitionEnd: () => setSnapping(false),
    onPointerDown: (event) => {
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
    },
    onPointerMove: (event) => {
      if (origin.current === null) return
      const raw = event.clientX - origin.current
      if (!dragged.current) {
        // The slop is measured on the raw delta, so a push toward a dead side
        // is still a drag: it cancels the long-press and swallows the click.
        if (Math.abs(raw) <= DRAG_SLOP_PX) return
        clearHold()
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
      // A dead side is clamped to zero: its band never shows, the row never moves.
      const max = onSwipeRight ? SWIPE_CLAMP_PX : 0
      const min = onSwipeLeft ? -SWIPE_CLAMP_PX : 0
      setDx(Math.max(min, Math.min(max, raw)))
    },
    onPointerUp: end,
    onPointerCancel: end,
    onClickCapture: (event) => {
      // `detail === 0` is a keyboard-activated click, which by definition
      // never followed a drag. `dragged` is only cleared by the next
      // pointerdown, so without this check a swipe would leave the row's
      // controls unreachable by keyboard until someone touched the row
      // again — the accessible path, broken by the accelerator.
      if (!dragged.current || event.detail === 0) return
      event.preventDefault()
      event.stopPropagation()
      dragged.current = false
    },
  }

  return { dx, snapping, handlers }
}
