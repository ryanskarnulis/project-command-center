import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../../../types/project'
import { ProjectChip } from './ProjectChip'

afterEach(cleanup)

function project(id: number, name: string): Project {
  return {
    id,
    name,
    description: null,
    system_key: null,
    sort_order: 0,
    is_protected: false,
    created_at: '',
    updated_at: '',
  }
}

const PROJECTS = [project(1, 'HomeNetwork'), project(2, 'Garden'), project(3, 'General')]

describe('ProjectChip', () => {
  it('shows the current project name on the trigger', () => {
    render(<ProjectChip value={2} projects={PROJECTS} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Project: Garden' })).toHaveTextContent(
      'Garden',
    )
  })

  it('filters projects as you type and picks one', () => {
    const onChange = vi.fn()
    render(<ProjectChip value={2} projects={PROJECTS} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Project: Garden' }))
    fireEvent.change(screen.getByLabelText('Search projects'), {
      target: { value: 'home' },
    })
    expect(screen.queryByRole('button', { name: 'General' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'HomeNetwork' }))
    expect(onChange).toHaveBeenCalledExactlyOnceWith(1)
  })

  it('never offers an Unassigned option (tasks are always filed)', () => {
    render(<ProjectChip value={2} projects={PROJECTS} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Project: Garden' }))
    expect(screen.queryByRole('button', { name: 'Unassigned' })).not.toBeInTheDocument()
  })

  it('does not fire when re-picking the current project', () => {
    const onChange = vi.fn()
    render(<ProjectChip value={2} projects={PROJECTS} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Project: Garden' }))
    fireEvent.click(screen.getByRole('button', { name: 'Garden' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
