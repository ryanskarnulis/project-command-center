import { Navigate, Route, Routes } from 'react-router-dom'
import { ProjectsPage } from '../features/projects/ProjectsPage'
import { TasksPage } from '../features/tasks/TasksPage'

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/projects" replace />} />
      <Route path="/projects" element={<ProjectsPage />} />
      <Route path="/projects/:projectId/tasks" element={<TasksPage />} />
    </Routes>
  )
}
