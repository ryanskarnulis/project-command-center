import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EstimateChip } from './EstimateChip'

afterEach(cleanup)

function openEditor(value: number | null, onChange = vi.fn()) {
  render(<EstimateChip value={value} onChange={onChange} />)
  fireEvent.click(screen.getByRole('button'))
  return onChange
}

describe('EstimateChip', () => {
  it('shows an empty-state affordance when unset', () => {
    render(<EstimateChip value={null} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Set estimate' })).toHaveTextContent('Estimate…')
  })

  it('focuses the editor input on open without scrolling it into view', () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, 'focus')
    openEditor(null)
    expect(screen.getByLabelText('Estimate')).toHaveFocus()
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true })
    focusSpy.mockRestore()
  })

  it('commits a parsed duration on submit', () => {
    const onChange = openEditor(null)
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: '2h' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(120)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('commits on Enter via form submit', () => {
    const onChange = openEditor(30)
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: '1 day' } })
    fireEvent.submit(screen.getByLabelText('Estimate'))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(1440)
  })

  it('shows an inline error for unparseable input and stays open', () => {
    const onChange = openEditor(30)
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: 'garbage' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(screen.getByRole('alert')).toHaveTextContent('30m, 2h, or 1 day')
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Estimate')).toBeInTheDocument()
  })

  it('clears the estimate when emptied', () => {
    const onChange = openEditor(30)
    fireEvent.change(screen.getByLabelText('Estimate'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(null)
  })

  it('does not fire when the value is unchanged', () => {
    const onChange = openEditor(120)
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
