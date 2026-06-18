import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createProject, listProjects } from '../../api/projects'
import { listAllTasks, listCompletedTasks } from '../../api/tasks'
import type { Project } from '../../types/project'
import { ProjectsPage } from './ProjectsPage'

vi.mock('../../api/projects', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  listAllTasks: vi.fn(),
  listCompletedTasks: vi.fn(),
}))

const projects: Project[] = [
  {
    id: 1,
    name: 'General',
    description: null,
    system_key: 'general',
    is_protected: true,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'Firewall',
    description: 'Edge hardening',
    system_key: null,
    is_protected: false,
    created_at: '2026-06-02T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
  },
]

const mockList = vi.mocked(listProjects)
const mockCreate = vi.mocked(createProject)
const mockListAll = vi.mocked(listAllTasks)
const mockListDone = vi.mocked(listCompletedTasks)

function renderPage() {
  return render(
    <MemoryRouter>
      <ProjectsPage />
    </MemoryRouter>,
  )
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList.mockResolvedValue(projects)
    mockCreate.mockResolvedValue(projects[1])
    mockListAll.mockResolvedValue([])
    mockListDone.mockResolvedValue([])
  })

  afterEach(cleanup)

  it('renders project cards linking to the detail hub', async () => {
    renderPage()
    expect(await screen.findByText('Firewall')).toBeInTheDocument()
    expect(screen.getByText('General')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Firewall' })).toHaveAttribute(
      'href',
      '/projects/2',
    )
  })

  it('creates a project through the New project modal', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Firewall')

    await user.click(screen.getByRole('button', { name: /New project/ }))
    await user.type(screen.getByLabelText('Name'), 'Backups')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        name: 'Backups',
        description: null,
      }),
    )
  })

  it('opens the edit modal from a card', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Firewall')

    await user.click(screen.getAllByRole('button', { name: 'Edit' })[0])

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toBeInTheDocument()
  })
})
