import { BrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { TrashCountProvider } from './features/trash/TrashCountContext'
import { AppRoutes } from './routes/AppRoutes'

function App() {
  return (
    <BrowserRouter>
      <TrashCountProvider>
        <AppShell>
          <AppRoutes />
        </AppShell>
      </TrashCountProvider>
    </BrowserRouter>
  )
}

export default App
