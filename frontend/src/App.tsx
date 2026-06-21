import { BrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { ToastProvider } from './components/ToastProvider'
import { TrashCountProvider } from './features/trash/TrashCountContext'
import { AppRoutes } from './routes/AppRoutes'

function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <TrashCountProvider>
          <AppShell>
            <AppRoutes />
          </AppShell>
        </TrashCountProvider>
      </ToastProvider>
    </BrowserRouter>
  )
}

export default App
