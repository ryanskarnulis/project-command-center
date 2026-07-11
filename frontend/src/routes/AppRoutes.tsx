import { Navigate, createBrowserRouter } from 'react-router-dom'
import { AgentPage } from '../features/agent/AgentPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { NotFoundPage } from '../features/errors/NotFoundPage'
import { RouteErrorBoundary } from '../features/errors/RouteErrorBoundary'
import { ProjectDetailPage } from '../features/projects/ProjectDetailPage'
import { TaskDetailRedirect } from '../features/tasks/TaskDetailRedirect'
import { TasksPage } from '../features/tasks/TasksPage'
import { FocusPage } from '../features/focus/FocusPage'
import { TrashPage } from '../features/trash/TrashPage'
import { AppLayout } from './AppLayout'

export const routes = [
  {
    element: <AppLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { path: '/', element: <Navigate to="/dashboard" replace /> },
      { path: '/dashboard', element: <DashboardPage /> },
      { path: '/focus', element: <FocusPage /> },
      { path: '/today', element: <Navigate to="/focus" replace /> },
      { path: '/tasks', element: <TasksPage /> },
      { path: '/tasks/:taskId', element: <TaskDetailRedirect /> },
      { path: '/projects', element: <Navigate to="/dashboard" replace /> },
      { path: '/projects/:projectId', element: <ProjectDetailPage /> },
      { path: '/projects/:projectId/tasks', element: <TasksPage /> },
      { path: '/agent', element: <AgentPage /> },
      { path: '/agent/:conversationId', element: <AgentPage /> },
      { path: '/trash', element: <TrashPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
