import type { ReactNode } from 'react'
import { SWIPE_COMMIT_PX, useSwipeGesture, type SwipeGestureOptions } from '../hooks/useSwipeGesture'

export interface SwipeReveal {
  /** Current translateX of the content (positive = dragged right). */
  dx: number
  /** 0–1: how far a right drag is toward committing. */
  right: number
  /** 0–1: how far a left drag is toward committing. */
  left: number
}

interface SwipeGestureRowProps extends SwipeGestureOptions {
  /** Feature CSS prefix: renders `<prefix>`, `<prefix>-content` and
   * `<prefix>-content snapping`, e.g. `focus-swipe`. */
  classPrefix: string
  /** The band behind the content, shown only while `enabled`. It is
   * decoration — mark it `aria-hidden`; the verbs live in the row's sheet. */
  renderReveal: (reveal: SwipeReveal) => ReactNode
  children: ReactNode
}

/**
 * One swipe row: a clipping cell, the reveal band behind, and the content that
 * translates under `useSwipeGesture`. Only the content cell moves — anything
 * outside it (Focus's clock gutter, say) stays pinned. `touch-action: pan-y`
 * on `<prefix>-content` leaves vertical scrolling to the browser.
 */
export function SwipeGestureRow({ classPrefix, renderReveal, children, ...options }: SwipeGestureRowProps) {
  const { dx, snapping, handlers } = useSwipeGesture(options)
  const content = `${classPrefix}-content`
  return (
    <div className={classPrefix}>
      {options.enabled && renderReveal({
        dx,
        right: Math.min(1, Math.max(0, dx) / SWIPE_COMMIT_PX),
        left: Math.min(1, Math.max(0, -dx) / SWIPE_COMMIT_PX),
      })}
      <div
        className={snapping ? `${content} snapping` : content}
        style={{ transform: `translateX(${dx}px)` }}
        {...handlers}
      >
        {children}
      </div>
    </div>
  )
}
