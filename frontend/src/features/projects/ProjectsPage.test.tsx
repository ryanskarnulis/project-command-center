import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createProject, deleteProject, listProjects } from '../../api/projects'
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
    sort_order: 0,
    is_protected: true,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'Firewall',
    description: 'Edge hardening',
    system_key: null,
    sort_order: 0,
    is_protected: false,
    created_at: '2026-06-02T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
  },
]

const mockList = vi.mocked(listProjects)
const mockCreate = vi.mocked(createProject)
const mockListAll = vi.mocked(listAllTasks)
const mockListDone = vi.mocked(listCompletedTasks)
const mockDelete = vi.mocked(deleteProject)

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
    mockDelete.mockResolvedValue(undefined)
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

  it('filters projects by search and clears it', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Firewall')

    await user.type(screen.getByLabelText('Search projects'), 'fire')
    expect(screen.getByText('Firewall')).toBeInTheDocument()
    expect(screen.queryByText('General')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Clear/ }))
    expect(screen.getByText('General')).toBeInTheDocument()
  })

  it('shows a no-match message when the search hides everything', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Firewall')

    await user.type(screen.getByLabelText('Search projects'), 'zzz')
    expect(screen.getByText('No projects match your search.')).toBeInTheDocument()
  })

  it('reorders projects when the sort changes', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('Firewall')

    // Default sort is by name → Firewall (F) before General (G).
    expect(screen.getAllByRole('link')[0]).toHaveTextContent('Firewall')

    // "Most open tasks" with all-zero counts keeps the original order → General first.
    await user.selectOptions(screen.getByLabelText('Sort projects'), 'open')
    expect(screen.getAllByRole('link')[0]).toHaveTextContent('General')
  })

  it('confirms before deleting a project', async () => {
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderPage()
    await screen.findByText('Firewall')

    // Only the non-protected project (Firewall, id 2) has a Delete button.
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(mockDelete).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(mockDelete).toHaveBeenCalledWith(2)

    confirmSpy.mockRestore()
  })

  it('shows an empty state when there are no projects', async () => {
    mockList.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No projects yet.')).toBeInTheDocument()
  })
})
