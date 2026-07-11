import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { createPortal } from 'react-dom'
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
 *
 * The popover renders in a portal with fixed positioning so it always sits on
 * the top layer — cards live inside overflow-clipped lanes/lists that would
 * otherwise cut it off.
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
  const [style, setStyle] = useState<CSSProperties>()
  const containerRef = useRef<HTMLSpanElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  const updatePosition = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    // Flip above the chip when there isn't room below (bottom-most cards).
    const height = popoverRef.current?.offsetHeight ?? 0
    const overflowsBelow = rect.bottom + 6 + height > window.innerHeight
    const fitsAbove = rect.top - 6 - height >= 0
    const vertical =
      overflowsBelow && fitsAbove
        ? { bottom: window.innerHeight - rect.top + 6 }
        : { top: rect.bottom + 6 }
    setStyle(
      align === 'right'
        ? { ...vertical, right: window.innerWidth - rect.right }
        : { ...vertical, left: rect.left },
    )
  }, [align])

  useLayoutEffect(() => {
    if (open) updatePosition()
  }, [open, updatePosition])

  // Close when a press lands outside the chip or the portaled popover. Scroll
  // and resize re-anchor instead of closing: on mobile, opening the popover
  // itself scrolls/resizes the viewport (the soft keyboard shows for an
  // autofocused editor input, or hides once the tap blurs a text field), and
  // closing on those events dismissed the popover the instant it opened.
  useEffect(() => {
    if (!open) return
    function isInside(target: EventTarget | null): boolean {
      const node = target instanceof Node ? target : null
      return Boolean(
        (node && containerRef.current?.contains(node)) ||
          (node && popoverRef.current?.contains(node)),
      )
    }
    function onPointerDown(e: MouseEvent) {
      if (!isInside(e.target)) setOpen(false)
    }
    function onReanchor(e: Event) {
      // Scrolling the popover's own lists doesn't move the anchor.
      if (isInside(e.target)) return
      updatePosition()
    }
    // Capture phase: enclosing surfaces (the peek panel) stopPropagation on
    // mousedown in the bubble phase, which would swallow outside presses
    // landing inside them and leave the popover stuck open.
    document.addEventListener('mousedown', onPointerDown, true)
    document.addEventListener('scroll', onReanchor, true)
    window.addEventListener('resize', onReanchor)
    return () => {
      document.removeEventListener('mousedown', onPointerDown, true)
      document.removeEventListener('scroll', onReanchor, true)
      window.removeEventListener('resize', onReanchor)
    }
  }, [open, updatePosition])

  const close = useCallback(() => setOpen(false), [])

  // Esc closes only the popover — stopPropagation keeps an enclosing
  // Esc-to-close surface (peek panel, modal) from also closing.
  function onKeyDown(e: ReactKeyboardEvent<HTMLElement>) {
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
      {open &&
        createPortal(
          <div
            ref={popoverRef}
            className="chip-popover"
            style={style}
            role="dialog"
            aria-label={label}
            onKeyDown={onKeyDown}
          >
            {typeof children === 'function' ? children(close) : children}
          </div>,
          document.body,
        )}
    </span>
  )
}
