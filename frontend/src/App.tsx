import { BrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AppRoutes } from './routes/AppRoutes'

function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <AppRoutes />
      </AppShell>
    </BrowserRouter>
  )
}

export default App
