import { RouterProvider } from 'react-router-dom'
import { ToastProvider } from './components/ToastProvider'
import { TrashCountProvider } from './features/trash/TrashCountContext'
import { TaskRefreshProvider } from './features/tasks/TaskRefreshProvider'
import { router } from './routes/AppRoutes'

function App() {
  return (
    <ToastProvider>
      <TrashCountProvider>
        <TaskRefreshProvider>
          <RouterProvider router={router} />
        </TaskRefreshProvider>
      </TrashCountProvider>
    </ToastProvider>
  )
}

export default App
