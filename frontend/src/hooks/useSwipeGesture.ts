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

/* The first-run hint (#320): one short peek toward each live side, played by
   the same transform and `.snapping` transition a real drag snaps back with. */
/** Wait after the row mounts before peeking, so the list (or sheet) has settled. */
export const SWIPE_HINT_DELAY_MS = 600
/** How far the peek slides — well short of `SWIPE_COMMIT_PX`, so it can never commit. */
export const SWIPE_HINT_PEEK_PX = 40
/** Each half of a peek (out, then back). */
export const SWIPE_HINT_STEP_MS = 450
const HINT_STORAGE_PREFIX = 'swipe-hint.'

/* Surfaces whose hint is spent this page load. Mirrors storage, so a viewer
   whose storage throws still sees it at most once per load rather than on
   every remount. */
const hintsSpent = new Set<string>()
/* Surfaces a mounted row has already claimed: every row of a list asks, the
   first eligible one to mount (React mounts siblings in order) wins. */
const hintsClaimed = new Set<string>()

function hintSpent(surface: string): boolean {
  if (hintsSpent.has(surface)) return true
  try {
    return localStorage.getItem(HINT_STORAGE_PREFIX + surface) !== null
  } catch {
    return false
  }
}

function spendHint(surface: string): void {
  hintsSpent.add(surface)
  try {
    localStorage.setItem(HINT_STORAGE_PREFIX + surface, 'seen')
  } catch {
    /* A per-viewer convenience: without storage it replays next load. */
  }
}

function motionAllowed(): boolean {
  // No matchMedia (jsdom) is treated as "don't animate" — every real browser has it.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

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
  /** Opt into the first-run hint under this surface key (e.g. `focus`). Every
   * row of a list may pass it: only the first enabled row to mount peeks,
   * once per surface per viewer (localStorage), never under
   * `prefers-reduced-motion`, never firing a verb. A press during it cancels. */
  hint?: string
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
 * The first-run hint (`hint`, #320) lives here rather than in a row: it plays
 * a short peek through this hook's own `dx`/`snapping` — the same transform
 * and snap-back the real drag uses — so every swipe row gets it. It only ever
 * moves the content; `origin` stays null, so no release can commit a verb.
 */
export function useSwipeGesture({ enabled, onSwipeRight, onSwipeLeft, onLongPress, hint }: SwipeGestureOptions): SwipeGesture {
  const [dx, setDx] = useState(0)
  const [snapping, setSnapping] = useState(false)
  const origin = useRef<number | null>(null)
  // A drag or a long-press that ends on a control must not also activate it.
  const dragged = useRef(false)
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Stops a scheduled or playing hint; null when none is pending.
  const cancelHint = useRef<(() => void) | null>(null)
  const hasRight = Boolean(onSwipeRight)
  const hasLeft = Boolean(onSwipeLeft)

  useEffect(() => () => { if (holdTimer.current) clearTimeout(holdTimer.current) }, [])

  useEffect(() => {
    if (!hint || !enabled || (!hasRight && !hasLeft)) return
    if (hintsClaimed.has(hint) || hintSpent(hint) || !motionAllowed()) return
    const surface = hint
    hintsClaimed.add(surface)
    let started = false
    let moved = false
    const timers: ReturnType<typeof setTimeout>[] = []
    const stop = (): void => {
      timers.forEach(clearTimeout)
      cancelHint.current = null
      // Unclaimed only if it never played: StrictMode's dev re-mount (and a
      // row that turns ineligible before its turn) hands the claim back.
      if (!started) hintsClaimed.delete(surface)
      if (moved) {
        moved = false
        setSnapping(false)
        setDx(0)
      }
    }
    timers.push(setTimeout(() => {
      started = true
      spendHint(surface)
      const sides = [...(hasRight ? [1] : []), ...(hasLeft ? [-1] : [])]
      const at = (ms: number, step: () => void): void => {
        if (ms === 0) step()
        else timers.push(setTimeout(step, ms))
      }
      sides.forEach((side, i) => {
        const out = i * 2 * SWIPE_HINT_STEP_MS
        at(out, () => { moved = true; setSnapping(true); setDx(side * SWIPE_HINT_PEEK_PX) })
        at(out + SWIPE_HINT_STEP_MS, () => { setSnapping(true); setDx(0) })
      })
      timers.push(setTimeout(() => { moved = false; cancelHint.current = null }, sides.length * 2 * SWIPE_HINT_STEP_MS))
    }, SWIPE_HINT_DELAY_MS))
    cancelHint.current = () => {
      // Touching the row before the peek counts as having found the gesture.
      started = true
      spendHint(surface)
      stop()
    }
    return stop
  }, [hint, enabled, hasRight, hasLeft])

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
      // A real press always wins over the hint: stop it and drop to rest so
      // the drag starts from zero under the finger.
      cancelHint.current?.()
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
