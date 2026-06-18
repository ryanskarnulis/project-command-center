import { Navigate, Route, Routes } from 'react-router-dom'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { InboxPage } from '../features/inbox/InboxPage'
import { ProjectDetailPage } from '../features/projects/ProjectDetailPage'
import { ProjectsPage } from '../features/projects/ProjectsPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { TaskDetailPage } from '../features/tasks/TaskDetailPage'
import { TasksPage } from '../features/tasks/TasksPage'
import { TrainingPage } from '../features/training/TrainingPage'
import { TrashPage } from '../features/trash/TrashPage'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/inbox" element={<InboxPage />} />
      <Route path="/inbox/:inboxId" element={<InboxPage />} />
      <Route path="/tasks" element={<TasksPage />} />
      <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
      <Route path="/projects" element={<ProjectsPage />} />
      <Route path="/projects/:projectId" element={<ProjectDetailPage />} />
      <Route path="/projects/:projectId/tasks" element={<TasksPage />} />
      <Route path="/training" element={<TrainingPage />} />
      <Route path="/trash" element={<TrashPage />} />
      <Route path="/settings" element={<SettingsPage />} />
    </Routes>
  )
}
