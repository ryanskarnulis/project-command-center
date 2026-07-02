import { Navigate, createBrowserRouter } from 'react-router-dom'
import { CalendarPage } from '../features/calendar/CalendarPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { NotFoundPage } from '../features/errors/NotFoundPage'
import { RouteErrorBoundary } from '../features/errors/RouteErrorBoundary'
import { InboxPage } from '../features/inbox/InboxPage'
import { ProjectDetailPage } from '../features/projects/ProjectDetailPage'
import { ProjectsPage } from '../features/projects/ProjectsPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { TaskDetailRedirect } from '../features/tasks/TaskDetailRedirect'
import { TasksPage } from '../features/tasks/TasksPage'
import { TodayPage } from '../features/today/TodayPage'
import { TrainingPage } from '../features/training/TrainingPage'
import { TrashPage } from '../features/trash/TrashPage'
import { AppLayout } from './AppLayout'

export const routes = [
  {
    element: <AppLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/', element: <Navigate to="/dashboard" replace /> },
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/today', element: <TodayPage /> },
      { path: '/calendar', element: <CalendarPage /> },
      { path: '/inbox', element: <InboxPage /> },
      { path: '/inbox/:inboxId', element: <InboxPage /> },
      { path: '/tasks', element: <TasksPage /> },
      { path: '/tasks/:taskId', element: <TaskDetailRedirect /> },
      { path: '/projects', element: <ProjectsPage /> },
      { path: '/projects/:projectId', element: <ProjectDetailPage /> },
      { path: '/projects/:projectId/tasks', element: <TasksPage /> },
      { path: '/training', element: <TrainingPage /> },
      { path: '/trash', element: <TrashPage /> },
      { path: '/settings', element: <SettingsPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
