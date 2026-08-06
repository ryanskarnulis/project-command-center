import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FormEvent } from 'react'
import { ChipPopover } from './ChipPopover'

afterEach(cleanup)

function renderChip(props: Partial<Parameters<typeof ChipPopover>[0]> = {}) {
  return render(
    <ChipPopover
      chip="high"
      chipClassName="priority-pill priority-high"
      label="Priority: high"
      {...props}
    >
      <span>Popover body</span>
    </ChipPopover>,
  )
}

describe('ChipPopover', () => {
  it('opens the popover on trigger click', () => {
    renderChip()
    expect(screen.queryByText('Popover body')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    expect(screen.getByRole('dialog', { name: 'Priority: high' })).toBeInTheDocument()
    expect(screen.getByText('Popover body')).toBeInTheDocument()
  })

  it('closes on a click outside', () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('Popover body')).not.toBeInTheDocument()
  })

  it('stays open on a click inside', () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    fireEvent.mouseDown(screen.getByText('Popover body'))
    expect(screen.getByText('Popover body')).toBeInTheDocument()
  })

  it('closes on an outside press even when an enclosing surface stops propagation', () => {
    // The peek panel stopPropagation()s mousedown in the bubble phase; the
    // outside-press listener must run in the capture phase so presses inside
    // the panel (but outside the popover) still dismiss it.
    render(
      <div onMouseDown={(e) => e.stopPropagation()}>
        <span>Panel chrome</span>
        <ChipPopover chip="high" chipClassName="priority-pill" label="Priority: high">
          <span>Popover body</span>
        </ChipPopover>
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    fireEvent.mouseDown(screen.getByText('Panel chrome'))
    expect(screen.queryByText('Popover body')).not.toBeInTheDocument()
  })

  it('stays open through a mobile tap on the trigger (touch + synthesized mouse events)', () => {
    renderChip()
    const trigger = screen.getByRole('button', { name: 'Priority: high' })
    // A tap fires touch events, then the browser synthesizes the mouse pair
    // and click. None of these may bounce the popover shut again.
    fireEvent.touchStart(trigger)
    fireEvent.touchEnd(trigger)
    fireEvent.mouseDown(trigger)
    fireEvent.mouseUp(trigger)
    fireEvent.click(trigger)
    expect(screen.getByText('Popover body')).toBeInTheDocument()
  })

  it('stays open when opening scrolls/resizes the viewport (mobile soft keyboard)', () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    // On mobile the soft keyboard shows for an autofocused editor input (or
    // hides once the tap blurs a text field) right after the popover opens,
    // firing document scroll and window resize. These must re-anchor the
    // popover, not dismiss it.
    fireEvent.scroll(document)
    fireEvent(window, new Event('resize'))
    expect(screen.getByText('Popover body')).toBeInTheDocument()
  })

  it('clamps to the viewport bottom when the popover fits neither below nor above', () => {
    // Short mobile viewport: a 300px popover under a chip at y=200 overflows
    // below and cannot flip above. It must pin inside the viewport instead of
    // extending past the bottom (which makes mobile browsers scroll the page
    // chasing the autofocused input).
    const originalInnerHeight = window.innerHeight
    Object.defineProperty(window, 'innerHeight', { value: 400, configurable: true })
    const offsetHeight = vi
      .spyOn(HTMLElement.prototype, 'offsetHeight', 'get')
      .mockReturnValue(300)
    renderChip()
    const trigger = screen.getByRole('button', { name: 'Priority: high' })
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      top: 200,
      bottom: 220,
      left: 40,
      right: 140,
      width: 100,
      height: 20,
      x: 40,
      y: 200,
      toJSON: () => ({}),
    } as DOMRect)
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Priority: high' })
    expect(dialog.style.top).toBe('94px') // 400 - 300 - 6, not 220 + 6
    expect(dialog.style.bottom).toBe('')
    offsetHeight.mockRestore()
    Object.defineProperty(window, 'innerHeight', {
      value: originalInnerHeight,
      configurable: true,
      writable: true,
    })
  })

  it('closes on Escape and refocuses the trigger without letting the event bubble', () => {
    const outerKeyDown = vi.fn()
    render(
      <div onKeyDown={outerKeyDown}>
        <ChipPopover chip="high" chipClassName="priority-pill" label="Priority: high">
          <span>Popover body</span>
        </ChipPopover>
      </div>,
    )
    const trigger = screen.getByRole('button', { name: 'Priority: high' })
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByText('Popover body'), { key: 'Escape' })
    expect(screen.queryByText('Popover body')).not.toBeInTheDocument()
    expect(outerKeyDown).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()
  })

  it('renders a disabled trigger with the hint as tooltip and does not open', () => {
    renderChip({ disabled: true, disabledHint: 'Rolled up from subtasks' })
    const trigger = screen.getByRole('button', { name: 'Priority: high' })
    expect(trigger).toBeDisabled()
    expect(trigger).toHaveAttribute('title', 'Rolled up from subtasks')
    fireEvent.click(trigger)
    expect(screen.queryByText('Popover body')).not.toBeInTheDocument()
  })

  it('keeps clicks inside the popover out of the enclosing React tree', () => {
    // The portal moves DOM ancestry to <body> but not React's event bubbling,
    // so a host like the task card's <Link> saw every option click and
    // navigated (#253). The trigger still bubbles — hosts guard that one.
    const outerClick = vi.fn()
    const optionClick = vi.fn()
    render(
      <div onClick={outerClick}>
        <ChipPopover chip="high" chipClassName="priority-pill" label="Priority: high">
          <button type="button" onClick={optionClick}>
            Low
          </button>
        </ChipPopover>
      </div>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Priority: high' }))
    expect(outerClick).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Low' }))
    expect(optionClick).toHaveBeenCalledOnce()
    expect(outerClick).toHaveBeenCalledOnce()
  })

  it('leaves native form submission inside the popover working', () => {
    // The click guard above must not preventDefault: the chip editors
    // (Estimate, Repeat) commit through a real submit button.
    const onSubmit = vi.fn((e: FormEvent<HTMLFormElement>) => e.preventDefault())
    render(
      <ChipPopover chip="x" chipClassName="estimate" label="Set estimate">
        <form onSubmit={onSubmit}>
          <button type="submit">Set</button>
        </form>
      </ChipPopover>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Set estimate' }))
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('passes a close callback to render-prop children', () => {
    render(
      <ChipPopover chip="x" chipClassName="estimate" label="Set estimate">
        {(close) => (
          <button type="button" onClick={close}>
            Done editing
          </button>
        )}
      </ChipPopover>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Set estimate' }))
    fireEvent.click(screen.getByRole('button', { name: 'Done editing' }))
    expect(screen.queryByText('Done editing')).not.toBeInTheDocument()
  })
})
