import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PriorityChip } from './PriorityChip'

afterEach(cleanup)

describe('PriorityChip', () => {
  it('shows the current priority on the trigger', () => {
    render(<PriorityChip value="high" onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Priority: high' })).toHaveTextContent('high')
  })

  it('offers all four priorities and fires onChange once for a new pick', () => {
    const onChange = vi.fn()
    render(<PriorityChip value="medium" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Priority: medium' }))
    for (const p of ['low', 'medium', 'high', 'urgent']) {
      expect(screen.getByRole('button', { name: p })).toBeInTheDocument()
    }
    fireEvent.click(screen.getByRole('button', { name: 'urgent' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('urgent')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('closes without firing when re-picking the current value', () => {
    const onChange = vi.fn()
    render(<PriorityChip value="medium" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Priority: medium' }))
    fireEvent.click(screen.getByRole('button', { name: 'medium' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
