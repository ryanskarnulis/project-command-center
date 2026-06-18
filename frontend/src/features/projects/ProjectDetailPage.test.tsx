import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getProject, updateProject } from '../../api/projects'
import { listTasks } from '../../api/tasks'
import type { Project } from '../../types/project'
import type { Task } from '../../types/task'
import { ProjectDetailPage } from './ProjectDetailPage'

vi.mock('../../api/projects', () => ({
  getProject: vi.fn(),
  updateProject: vi.fn(),
}))

vi.mock('../../api/tasks', () => ({
  listTasks: vi.fn(),
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
  confidence: null,
  assignee_hint: null,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  is_blocked: false,
}

const mockGetProject = vi.mocked(getProject)
const mockUpdateProject = vi.mocked(updateProject)
const mockListTasks = vi.mocked(listTasks)

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
    mockUpdateProject.mockImplementation(async (_id, patch) => ({ ...project, ...patch }))
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
})
