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

vi.mock('../features/settings/SettingsPage', () => ({
  SettingsPage: () => <main>Settings page</main>,
}))

vi.mock('../features/calendar/CalendarPage', () => ({
  CalendarPage: () => <main>Calendar page</main>,
}))

vi.mock('../features/inbox/InboxPage', () => ({
  InboxPage: () => <main>Inbox page</main>,
}))

vi.mock('../features/projects/ProjectDetailPage', () => ({
  ProjectDetailPage: () => <main>Project detail page</main>,
}))

vi.mock('../features/projects/ProjectsPage', () => ({
  ProjectsPage: () => <main>Projects page</main>,
}))

vi.mock('../features/tasks/TaskDetailPage', () => ({
  TaskDetailPage: () => <main>Task detail page</main>,
}))

vi.mock('../features/today/TodayPage', () => ({
  TodayPage: () => <main>Today page</main>,
}))

vi.mock('../features/training/TrainingPage', () => ({
  TrainingPage: () => <main>Training page</main>,
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

  it('renders task and settings routes inside the shell layout', async () => {
    const taskView = renderRoute('/tasks')
    expect(await screen.findByText('Tasks page')).toBeInTheDocument()
    expect(screen.getByText('Command search')).toBeInTheDocument()
    taskView.unmount()

    renderRoute('/settings')
    expect(await screen.findByText('Settings page')).toBeInTheDocument()
    expect(screen.getByText('Local-first workspace. No cloud sync configured.')).toBeInTheDocument()
  })
})
