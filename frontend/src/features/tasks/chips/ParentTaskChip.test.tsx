import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ParentTaskChip } from './ParentTaskChip'

afterEach(cleanup)

const OPTIONS = [
  { id: 11, label: 'Rebuild firewall' },
  { id: 12, label: 'Patch the router' },
]

describe('ParentTaskChip', () => {
  it('shows an empty-state affordance when the task has no parent', () => {
    render(<ParentTaskChip value={null} options={OPTIONS} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Set parent task' })).toHaveTextContent(
      'Set parent',
    )
  })

  it('shows the parent title and picks a new parent via search', () => {
    const onChange = vi.fn()
    render(<ParentTaskChip value={11} options={OPTIONS} onChange={onChange} />)
    const trigger = screen.getByRole('button', { name: 'Parent task: Rebuild firewall' })
    expect(trigger).toHaveTextContent('Sub of Rebuild firewall')
    fireEvent.click(trigger)
    fireEvent.change(screen.getByLabelText('Search tasks'), { target: { value: 'router' } })
    expect(
      screen.queryByRole('button', { name: 'Rebuild firewall' }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Patch the router' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(12)
  })

  it('focuses the search input on open without scrolling it into view', () => {
    // preventScroll matters: the input is focused before ChipPopover anchors
    // the portaled popover, and a plain focus() makes mobile browsers scroll
    // the page to the bottom chasing it.
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus')
    render(<ParentTaskChip value={null} options={OPTIONS} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set parent task' }))
    expect(screen.getByLabelText('Search tasks')).toHaveFocus()
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true })
    focusSpy.mockRestore()
  })

  it('clears the parent via None', () => {
    const onChange = vi.fn()
    render(<ParentTaskChip value={11} options={OPTIONS} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Parent task: Rebuild firewall' }))
    fireEvent.click(screen.getByRole('button', { name: 'None' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null)
  })
})
