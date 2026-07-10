import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Project } from '../../types/project'
import { ProjectFormModal } from './ProjectFormModal'

const project: Project = {
  id: 5,
  name: 'Firewall',
  description: 'Edge hardening',
  system_key: null,
  sort_order: 0,
  is_protected: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
}

describe('ProjectFormModal', () => {
  afterEach(cleanup)

  it('creates a project', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    const onClose = vi.fn()
    render(<ProjectFormModal mode="create" onSave={onSave} onClose={onClose} />)

    await user.type(screen.getByLabelText('Name'), 'Backups')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(onSave).toHaveBeenCalledWith({ name: 'Backups', description: null })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('edits a project seeded from its current values', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<ProjectFormModal mode="edit" project={project} onSave={onSave} onClose={vi.fn()} />)

    const name = screen.getByLabelText('Name')
    expect(name).toHaveValue('Firewall')
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(onSave).toHaveBeenCalledWith(5, {
      name: 'Edge Firewall',
      description: 'Edge hardening',
    })
  })
})
