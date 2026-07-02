import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DueDateChip } from './DueDateChip'

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 6, 15)) // Wed Jul 15 2026
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('DueDateChip', () => {
  it('shows an empty-state affordance when unset', () => {
    render(<DueDateChip value={null} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Set due date' })).toHaveTextContent(
      'Set due date',
    )
  })

  it('sets today via the preset', () => {
    const onChange = vi.fn()
    render(<DueDateChip value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set due date' }))
    fireEvent.click(screen.getByRole('button', { name: 'Today' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-07-15')
  })

  it('sets tomorrow and next week via presets', () => {
    const onChange = vi.fn()
    render(<DueDateChip value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set due date' }))
    fireEvent.click(screen.getByRole('button', { name: 'Tomorrow' }))
    expect(onChange).toHaveBeenLastCalledWith('2026-07-16')

    fireEvent.click(screen.getByRole('button', { name: 'Set due date' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next week' }))
    expect(onChange).toHaveBeenLastCalledWith('2026-07-22')
  })

  it('picks a day from the calendar grid', () => {
    const onChange = vi.fn()
    render(<DueDateChip value="2026-07-10" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Due date/ }))
    fireEvent.click(screen.getByRole('button', { name: '2026-07-22' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('2026-07-22')
  })

  it('marks the current value in the grid', () => {
    render(<DueDateChip value="2026-07-10" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Due date/ }))
    expect(screen.getByRole('button', { name: '2026-07-10' })).toHaveAttribute(
      'aria-current',
      'date',
    )
  })

  it('navigates months', () => {
    render(<DueDateChip value={null} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set due date' }))
    expect(screen.getByText('July 2026')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next month' }))
    expect(screen.getByText('August 2026')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2026-08-03' }))
  })

  it('clears the date; Clear is hidden when already unset', () => {
    const onChange = vi.fn()
    render(<DueDateChip value="2026-07-10" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Due date/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null)

    cleanup()
    render(<DueDateChip value={null} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set due date' }))
    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument()
  })

  it('does not fire when re-picking the current value', () => {
    const onChange = vi.fn()
    render(<DueDateChip value="2026-07-10" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Due date/ }))
    fireEvent.click(screen.getByRole('button', { name: '2026-07-10' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
