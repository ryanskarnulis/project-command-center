import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getProjectSummary } from '../../api/dashboard'
import {
  createAlias,
  deleteAlias,
  getProject,
  getProjectActivity,
  listAliases,
  updateProject,
} from '../../api/projects'
import { listCompletedTasks, listTasks } from '../../api/tasks'
import type { Project, ProjectAlias } from '../../types/project'
import type { Task } from '../../types/task'
import { ProjectDetailPage } from './ProjectDetailPage'

vi.mock('../../api/projects', () => ({
  getProject: vi.fn(),
  updateProject: vi.fn(),
  listAliases: vi.fn(),
  createAlias: vi.fn(),
  deleteAlias: vi.fn(),
  getProjectActivity: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  listTasks: vi.fn(),
  listCompletedTasks: vi.fn(),
}))

vi.mock('../../api/dashboard', () => ({
  getProjectSummary: vi.fn(),
}))

const project: Project = {
  id: 7,
  name: 'Firewall',
  description: 'Edge hardening',
  system_key: null,
  is_protected: false,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
}

const task: Task = {
  id: 3,
  project_id: 7,
  inbox_item_id: null,
  parent_task_id: null,
  title: 'Patch the router',
  description: null,
  review_status: 'accepted',
  workflow_status: 'open',
  priority: 'high',
  due_date: null,
  estimated_minutes: null,
  repeat_interval: null,
  recurrence_id: null,
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
  has_subtasks: false,
}

const alias: ProjectAlias = {
  id: 11,
  project_id: 7,
  alias: 'fw',
  created_at: '2026-06-01T00:00:00Z',
}

const mockGetProject = vi.mocked(getProject)
const mockUpdateProject = vi.mocked(updateProject)
const mockListTasks = vi.mocked(listTasks)
const mockListCompleted = vi.mocked(listCompletedTasks)
const mockListAliases = vi.mocked(listAliases)
const mockCreateAlias = vi.mocked(createAlias)
const mockDeleteAlias = vi.mocked(deleteAlias)
const mockGetProjectActivity = vi.mocked(getProjectActivity)
const mockGetProjectSummary = vi.mocked(getProjectSummary)

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/projects/7']}>
      <Routes>
        <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ProjectDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetProject.mockResolvedValue(project)
    mockListTasks.mockResolvedValue([task])
    mockListCompleted.mockResolvedValue([])
    mockUpdateProject.mockImplementation(async (_id, patch) => ({ ...project, ...patch }))
    mockListAliases.mockResolvedValue([])
    mockGetProjectActivity.mockResolvedValue([])
  })

  afterEach(cleanup)

  it('renders the project name, its tasks, and a View-all link', async () => {
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    expect(await screen.findByText('Patch the router')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /View all tasks/ }),
    ).toHaveAttribute('href', '/projects/7/tasks')
  })

  it('saves the name inline on blur', async () => {
    const user = userEvent.setup()
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    await user.clear(name)
    await user.type(name, 'Edge Firewall')
    await user.tab()

    await waitFor(() =>
      expect(mockUpdateProject).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ name: 'Edge Firewall' }),
      ),
    )
  })

  it('does not save when the name is cleared to blank', async () => {
    const user = userEvent.setup()
    renderDetail()

    const name = await screen.findByLabelText('Project name')
    await waitFor(() => expect(name).toHaveValue('Firewall'))
    await user.clear(name)
    await user.tab()

    expect(mockUpdateProject).not.toHaveBeenCalled()
    expect(await screen.findByText('Name is required')).toBeInTheDocument()
  })

  it('generates an AI summary on demand', async () => {
    const user = userEvent.setup()
    mockGetProjectSummary.mockResolvedValue({
      project_id: 7,
      summary: 'Two open tasks remain on the edge firewall.',
      model_name: 'gemma4:e2b',
    })
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Summarize' }))

    expect(mockGetProjectSummary).toHaveBeenCalledWith(7)
    expect(
      await screen.findByText('Two open tasks remain on the edge firewall.'),
    ).toBeInTheDocument()
  })

  it('adds and removes aliases via the dedicated endpoints', async () => {
    const user = userEvent.setup()
    mockListAliases.mockResolvedValue([alias])
    mockCreateAlias.mockResolvedValue({
      id: 12,
      project_id: 7,
      alias: 'firewall',
      created_at: '2026-06-01T00:05:00Z',
    })
    mockDeleteAlias.mockResolvedValue(undefined)
    renderDetail()

    // Existing alias loads.
    expect(await screen.findByText('fw')).toBeInTheDocument()

    // Add a new one.
    await user.type(screen.getByLabelText('Add alias'), 'firewall')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    expect(mockCreateAlias).toHaveBeenCalledWith(7, { alias: 'firewall' })
    expect(await screen.findByText('firewall')).toBeInTheDocument()

    // Remove the original.
    await user.click(screen.getByRole('button', { name: 'Remove alias fw' }))
    expect(mockDeleteAlias).toHaveBeenCalledWith(7, 11)
    await waitFor(() => expect(screen.queryByText('fw')).not.toBeInTheDocument())
  })

  it('mounts the activity feed for the project', async () => {
    const user = userEvent.setup()
    renderDetail()

    await user.click(await screen.findByRole('button', { name: 'Activity' }))
    expect(await screen.findByText('No activity yet.')).toBeInTheDocument()
    expect(mockGetProjectActivity).toHaveBeenCalled()
  })
})
