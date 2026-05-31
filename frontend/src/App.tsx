import { BrowserRouter, NavLink } from 'react-router-dom'
import { AppRoutes } from './routes/AppRoutes'

function App() {
  return (
    <BrowserRouter>
      <nav className="app-nav">
        <NavLink to="/inbox">Inbox</NavLink>
        <NavLink to="/projects">Projects</NavLink>
      </nav>
      <AppRoutes />
    </BrowserRouter>
  )
}

export default App
