import { useEffect, useRef, type ReactNode } from 'react'
import { DRAG_SLOP_PX, LONG_PRESS_MS } from '../../../hooks/useSwipeGesture'

interface MicLongPressProps {
  /** A `LONG_PRESS_MS` still hold on the mic (#326). */
  onLongPress: () => void
  /** Spoken replies are off — the mic carries a small muted mark (CSS). */
  muted: boolean
  /** The vendored `MicButton`, which this wraps rather than edits. */
  children: ReactNode
}

/**
 * Hold the composer mic to flip spoken replies — the mute a long hands-free
 * session wants without opening the `⋯` sheet, which stays the accessible
 * path to the same switch.
 *
 * `MicButton` is vendored ("re-copy, never edit"), so the gesture lives on
 * this wrapper: pointer events bubble up to it, and a capture-phase click
 * handler swallows the click a long-press release produces before it can
 * reach the mic's own tap (start/stop a voice session). A short tap, and any
 * keyboard activation (`detail === 0`), passes through untouched.
 *
 * The wrapper is `display: contents`, so the mic and its error line stay
 * direct items of the composer grid exactly as before.
 */
export function MicLongPress({ onLongPress, muted, children }: MicLongPressProps) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const origin = useRef<{ x: number; y: number } | null>(null)
  // Set when the hold fires; cleared by the swallowed click or the next press
  // (a touch long-press may never produce a click at all).
  const fired = useRef(false)

  const clear = (): void => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    origin.current = null
  }

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  return (
    <span
      className={`magent-mic${muted ? ' magent-mic--muted' : ''}`}
      onPointerDown={(event) => {
        fired.current = false
        if (event.button !== 0) return
        clear()
        origin.current = { x: event.clientX, y: event.clientY }
        timer.current = setTimeout(() => {
          timer.current = null
          origin.current = null
          fired.current = true
          navigator.vibrate?.(12)
          onLongPress()
        }, LONG_PRESS_MS)
      }}
      onPointerMove={(event) => {
        const start = origin.current
        if (!start) return
        if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > DRAG_SLOP_PX) clear()
      }}
      onPointerUp={clear}
      onPointerCancel={clear}
      onPointerLeave={clear}
      onClickCapture={(event) => {
        if (!fired.current || event.detail === 0) return
        fired.current = false
        event.preventDefault()
        event.stopPropagation()
      }}
      // A held touch would otherwise open the browser's context menu.
      onContextMenu={(event) => event.preventDefault()}
    >
      {children}
    </span>
  )
}
