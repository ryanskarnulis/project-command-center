import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssigneeChip } from './AssigneeChip'

afterEach(cleanup)

describe('AssigneeChip', () => {
  it('shows an empty-state affordance when unassigned', () => {
    render(<AssigneeChip value={null} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Set assignee' })).toHaveTextContent('Assign…')
  })

  it('commits a trimmed assignee on submit', () => {
    const onChange = vi.fn()
    render(<AssigneeChip value={null} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Set assignee' }))
    fireEvent.change(screen.getByLabelText('Assignee'), { target: { value: '  ryan  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith('ryan')
  })

  it('clears the assignee when emptied', () => {
    const onChange = vi.fn()
    render(<AssigneeChip value="ryan" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Assignee: ryan' }))
    fireEvent.change(screen.getByLabelText('Assignee'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null)
  })

  it('does not fire when the value is unchanged', () => {
    const onChange = vi.fn()
    render(<AssigneeChip value="ryan" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Assignee: ryan' }))
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
