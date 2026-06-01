import { BrowserRouter, NavLink } from 'react-router-dom'
import { AppRoutes } from './routes/AppRoutes'

function App() {
  return (
    <BrowserRouter>
      <nav className="app-nav">
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/inbox">Inbox</NavLink>
        <NavLink to="/projects">Projects</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <AppRoutes />
    </BrowserRouter>
  )
}

export default App
