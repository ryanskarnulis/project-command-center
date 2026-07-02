import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
