import { Navigate, createBrowserRouter } from 'react-router-dom'
import { CalendarPage } from '../features/calendar/CalendarPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { InboxPage } from '../features/inbox/InboxPage'
import { ProjectDetailPage } from '../features/projects/ProjectDetailPage'
import { ProjectsPage } from '../features/projects/ProjectsPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { TimelinePage } from '../features/planning/TimelinePage'
import { TaskDetailPage } from '../features/tasks/TaskDetailPage'
import { TasksPage } from '../features/tasks/TasksPage'
import { TodayPage } from '../features/today/TodayPage'
import { TrainingPage } from '../features/training/TrainingPage'
import { TrashPage } from '../features/trash/TrashPage'
import { AppLayout } from './AppLayout'

export const routes = [
  {
    element: <AppLayout />,
    children: [
      { path: '/', element: <Navigate to="/dashboard" replace /> },
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/today', element: <TodayPage /> },
      { path: '/calendar', element: <CalendarPage /> },
      { path: '/inbox', element: <InboxPage /> },
      { path: '/inbox/:inboxId', element: <InboxPage /> },
      { path: '/tasks', element: <TasksPage /> },
      { path: '/tasks/:taskId', element: <TaskDetailPage /> },
      { path: '/projects', element: <ProjectsPage /> },
      { path: '/projects/:projectId', element: <ProjectDetailPage /> },
      { path: '/projects/:projectId/tasks', element: <TasksPage /> },
      { path: '/projects/:projectId/timeline', element: <TimelinePage /> },
      { path: '/training', element: <TrainingPage /> },
      { path: '/trash', element: <TrashPage /> },
      { path: '/settings', element: <SettingsPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
