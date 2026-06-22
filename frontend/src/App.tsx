import { RouterProvider } from 'react-router-dom'
import { ToastProvider } from './components/ToastProvider'
import { TrashCountProvider } from './features/trash/TrashCountContext'
import { router } from './routes/AppRoutes'

function App() {
  return (
    <ToastProvider>
      <TrashCountProvider>
        <RouterProvider router={router} />
      </TrashCountProvider>
    </ToastProvider>
  )
}

export default App
