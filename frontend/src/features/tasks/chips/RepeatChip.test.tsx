import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RepeatChip } from './RepeatChip'

afterEach(cleanup)

describe('RepeatChip', () => {
  it('shows an empty-state affordance when not recurring', () => {
    render(<RepeatChip value={null} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Set repeat' })).toHaveTextContent('Repeat…')
  })

  it('focuses the editor input on open without scrolling it into view', () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus')
    render(<RepeatChip value={null} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set repeat' }))
    expect(screen.getByLabelText('Repeat')).toHaveFocus()
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true })
    focusSpy.mockRestore()
  })

  it('commits a parsed interval on submit', () => {
    const onChange = vi.fn()
    render(<RepeatChip value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set repeat' }))
    fireEvent.change(screen.getByLabelText('Repeat'), { target: { value: 'every 2 weeks' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith({ unit: 'week', every: 2 })
  })

  it('shows an inline error for unrecognized text and stays open', () => {
    const onChange = vi.fn()
    render(<RepeatChip value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set repeat' }))
    fireEvent.change(screen.getByLabelText('Repeat'), { target: { value: 'fortnightly' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(screen.getByRole('alert')).toHaveTextContent('every 2 weeks')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('clears recurrence when emptied', () => {
    const onChange = vi.fn()
    render(<RepeatChip value={{ unit: 'week', every: 1 }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Repeat: weekly' }))
    fireEvent.change(screen.getByLabelText('Repeat'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null)
  })

  it('does not fire when the interval is unchanged', () => {
    const onChange = vi.fn()
    render(<RepeatChip value={{ unit: 'week', every: 1 }} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Repeat: weekly' }))
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is read-only with a hint until the task has a due date', () => {
    render(
      <RepeatChip
        value={null}
        onChange={vi.fn()}
        disabled
        disabledHint="Set a due date to enable recurrence"
      />,
    )
    const trigger = screen.getByRole('button', { name: 'Set repeat' })
    expect(trigger).toBeDisabled()
    expect(trigger).toHaveAttribute('title', 'Set a due date to enable recurrence')
  })
})
