import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StatusChip } from './StatusChip'

afterEach(cleanup)

describe('StatusChip', () => {
  it('shows the current status and offers the three workflow states', () => {
    const onChange = vi.fn()
    render(<StatusChip value="open" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Status: Open' }))
    expect(screen.getByRole('button', { name: 'In progress' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Done' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'In progress' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('in_progress')
  })

  it('omits the skip item without a callback', () => {
    render(<StatusChip value="open" onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Status: Open' }))
    expect(screen.queryByText('Skip occurrence…')).not.toBeInTheDocument()
  })

  it('fires onSkipOccurrence from the skip item without a status change', () => {
    const onChange = vi.fn()
    const onSkip = vi.fn()
    render(<StatusChip value="open" onChange={onChange} onSkipOccurrence={onSkip} />)
    fireEvent.click(screen.getByRole('button', { name: 'Status: Open' }))
    fireEvent.click(screen.getByText('Skip occurrence…'))
    expect(onSkip).toHaveBeenCalledOnce()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('is read-only with a hint when disabled (subtask rollup)', () => {
    render(
      <StatusChip
        value="in_progress"
        onChange={vi.fn()}
        disabled
        disabledHint="Rolled up from subtasks"
      />,
    )
    const trigger = screen.getByRole('button', { name: 'Status: In progress' })
    expect(trigger).toBeDisabled()
    expect(trigger).toHaveAttribute('title', 'Rolled up from subtasks')
  })
})
