import { type ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'

interface BottomSheetProps {
  /** Accessible name. Pass `labelledBy` instead when the sheet has a visible heading. */
  label?: string
  labelledBy?: string
  /** Extra class on the dialog for per-sheet content styling. */
  className?: string
  /** Accessible name of the grab handle, which also dismisses on tap or swipe-down. */
  handleLabel: string
  onClose: () => void
  children: ReactNode
}

/**
 * Phone bottom sheet (M02f filter sheet, M06f project actions). A native modal
 * `<dialog>` supplies focus containment, Escape and background inertness; the
 * scrim is its `::backdrop`, and a tap on it dismisses.
 */
export function BottomSheet({ label, labelledBy, className, handleLabel, onClose, children }: BottomSheetProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const swipeStart = useRef<number | null>(null)

  useEffect(() => {
    const element = dialog.current!
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overflow = document.body.style.overflow
    element.showModal()
    document.body.style.overflow = 'hidden'
    return () => {
      element.close()
      document.body.style.overflow = overflow
      previousFocus?.focus()
    }
  }, [])

  return createPortal(
    <dialog
      ref={dialog}
      className={className ? `bottom-sheet ${className}` : 'bottom-sheet'}
      aria-label={label}
      aria-labelledby={labelledBy}
      onCancel={(event) => { event.preventDefault(); onClose() }}
      // Escape arrives as `cancel` above, but a close that skips it (Chromium's
      // non-cancelable close-watcher path, `requestClose`) must still reach the
      // owner, or the element closes while React believes the sheet is open.
      onClose={onClose}
      onClick={(event) => {
        // The dialog element is the click target only when the tap landed on
        // its backdrop (the scrim), which is outside the sheet's own box.
        if (event.target !== event.currentTarget) return
        const rect = event.currentTarget.getBoundingClientRect()
        if (event.clientY < rect.top || event.clientX < rect.left || event.clientX > rect.right || event.clientY > rect.bottom) onClose()
      }}
    >
      <div className="bottom-sheet-content">
        <button
          type="button"
          className="bottom-sheet-handle"
          aria-label={handleLabel}
          onClick={onClose}
          onPointerDown={(event) => {
            swipeStart.current = event.clientY
            event.currentTarget.setPointerCapture?.(event.pointerId)
          }}
          onPointerUp={(event) => {
            if (swipeStart.current !== null && event.clientY - swipeStart.current > 45) onClose()
            swipeStart.current = null
          }}
          onPointerCancel={() => { swipeStart.current = null }}
        ><span /></button>
        {children}
      </div>
    </dialog>,
    document.body,
  )
}
