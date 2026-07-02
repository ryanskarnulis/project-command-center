import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'

interface Props {
  /** Pill content shown on the trigger button. */
  chip: ReactNode
  /** Pill classes styling the trigger, e.g. `priority-pill priority-high`. */
  chipClassName: string
  /** Accessible name for the trigger and popover, e.g. "Priority: high". */
  label: string
  disabled?: boolean
  /** Tooltip explaining why the chip is read-only. */
  disabledHint?: string
  /** Right-align the popover when the chip sits near a right edge. */
  align?: 'left' | 'right'
  /** Popover body; the render-prop form receives a `close` callback. */
  children: ReactNode | ((close: () => void) => ReactNode)
}

/**
 * A metadata pill that opens an anchored editor on click. The popover body is
 * mounted only while open, so editors can hold draft state without staleness.
 */
export function ChipPopover({
  chip,
  chipClassName,
  label,
  disabled = false,
  disabledHint,
  align = 'left',
  children,
}: Props) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLSpanElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  // Close when a click lands outside the chip (same pattern as CommandSearch).
  useEffect(() => {
    if (!open) return
    function onPointerDown(e: MouseEvent) {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [open])

  const close = useCallback(() => setOpen(false), [])

  // Esc closes only the popover — stopPropagation keeps an enclosing
  // Esc-to-close surface (peek panel, modal) from also closing.
  function onKeyDown(e: ReactKeyboardEvent<HTMLSpanElement>) {
    if (e.key === 'Escape' && open) {
      e.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
  }

  return (
    <span className="chip-wrap" ref={containerRef} onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        className={`chip-trigger ${chipClassName}`}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        title={disabled ? disabledHint : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        {chip}
      </button>
      {open && (
        <div
          className={`chip-popover${align === 'right' ? ' chip-popover--right' : ''}`}
          role="dialog"
          aria-label={label}
        >
          {typeof children === 'function' ? children(close) : children}
        </div>
      )}
    </span>
  )
}
