import { render, screen } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { routes } from './AppRoutes'

vi.mock('../features/search/CommandSearch', () => ({
  CommandSearch: () => <div>Command search</div>,
}))

vi.mock('../features/dashboard/DashboardPage', () => ({
  DashboardPage: () => <main>Dashboard page</main>,
}))

vi.mock('../features/tasks/TasksPage', () => ({
  TasksPage: () => <main>Tasks page</main>,
}))

vi.mock('../features/projects/ProjectDetailPage', () => ({
  ProjectDetailPage: () => <main>Project detail page</main>,
}))

vi.mock('../features/focus/FocusPage', () => ({
  FocusPage: () => <main>Focus page</main>,
}))

vi.mock('../features/trash/TrashPage', () => ({
  TrashPage: () => <main>Trash page</main>,
}))

function renderRoute(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(<RouterProvider router={router} />)
}

describe('AppRoutes', () => {
  it('redirects the root route into the dashboard inside the shell', async () => {
    renderRoute('/')

    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
    expect(screen.getByText('Project')).toBeInTheDocument()
    expect(screen.getAllByText('Command Center').length).toBeGreaterThan(0)
  })

  it('renders a friendly in-app 404 inside the shell for unknown routes', async () => {
    renderRoute('/nonexistent')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    // Stays inside the app shell so the user can navigate out.
    expect(screen.getByText('Command search')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the dashboard' })).toHaveAttribute(
      'href',
      '/dashboard',
    )
  })

  it('renders the tasks route inside the shell layout', async () => {
    renderRoute('/tasks')
    expect(await screen.findByText('Tasks page')).toBeInTheDocument()
    expect(screen.getByText('Command search')).toBeInTheDocument()
  })

  it('redirects the legacy /projects list route to /dashboard', async () => {
    const router = createMemoryRouter(routes, { initialEntries: ['/projects'] })
    render(<RouterProvider router={router} />)

    expect(await screen.findByText('Dashboard page')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/dashboard')
  })

  it('redirects the legacy /today route to /focus', async () => {
    const router = createMemoryRouter(routes, { initialEntries: ['/today'] })
    render(<RouterProvider router={router} />)

    expect(await screen.findByText('Focus page')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/focus')
  })

  it('redirects /tasks/:id deep links onto the Tasks page (peek panel)', async () => {
    const router = createMemoryRouter(routes, { initialEntries: ['/tasks/7'] })
    render(<RouterProvider router={router} />)

    expect(await screen.findByText('Tasks page')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/tasks')
    expect(router.state.location.search).toBe('?task=7')
  })
})
