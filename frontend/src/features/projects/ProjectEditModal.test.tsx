import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createAlias, deleteAlias, listAliases } from '../../api/projects'
import type { Project, ProjectAlias } from '../../types/project'
import { ProjectEditModal } from './ProjectEditModal'

vi.mock('../../api/projects', () => ({
  createAlias: vi.fn(),
  deleteAlias: vi.fn(),
  listAliases: vi.fn(),
}))

const mockCreateAlias = vi.mocked(createAlias)
const mockDeleteAlias = vi.mocked(deleteAlias)
const mockListAliases = vi.mocked(listAliases)

const project: Project = {
  id: 7,
  name: 'Firewall',
  description: null,
  system_key: null,
  is_protected: false,
  created_at: '2026-06-01T17:00:00Z',
  updated_at: '2026-06-01T17:00:00Z',
}

const existingAlias: ProjectAlias = {
  id: 11,
  project_id: project.id,
  alias: 'fw',
  created_at: '2026-06-01T17:00:00Z',
}

describe('ProjectEditModal aliases', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockListAliases.mockResolvedValue([existingAlias])
  })

  it('adds and removes aliases via the dedicated endpoints', async () => {
    const user = userEvent.setup()
    mockCreateAlias.mockResolvedValue({
      id: 12,
      project_id: project.id,
      alias: 'firewall',
      created_at: '2026-06-01T17:05:00Z',
    })
    mockDeleteAlias.mockResolvedValue(undefined)

    render(
      <ProjectEditModal project={project} onClose={vi.fn()} onSave={vi.fn()} />,
    )

    // Existing alias loads.
    expect(await screen.findByText('fw')).toBeInTheDocument()
    expect(mockListAliases).toHaveBeenCalledWith(project.id)

    // Add a new alias.
    await user.type(screen.getByLabelText('Add alias'), 'firewall')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(mockCreateAlias).toHaveBeenCalledWith(project.id, { alias: 'firewall' })
    expect(await screen.findByText('firewall')).toBeInTheDocument()

    // Remove the original alias.
    await user.click(screen.getByRole('button', { name: 'Remove alias fw' }))

    expect(mockDeleteAlias).toHaveBeenCalledWith(project.id, existingAlias.id)
    await waitFor(() =>
      expect(screen.queryByText('fw')).not.toBeInTheDocument(),
    )
  })
})
