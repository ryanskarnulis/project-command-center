import { type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useTrashCount } from '../features/trash/trashCountContext'
import { CommandSearch } from '../features/search/CommandSearch'
import { CheckSquare, Home, Sun, Trash2 } from 'lucide-react'
import { GatewayLink } from './GatewayLink'
import { GlitchMark } from './GlitchMark'

interface AppShellProps {
  children: ReactNode
}

const topbarNav = [
  { to: '/focus', label: 'Focus', icon: Sun },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
  { to: '/agent', label: 'Agent', icon: GlitchMark },
]

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'shell-nav-link active' : 'shell-nav-link'
}

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-cluster">
          <GatewayLink />
          {/* The web mark belongs to the gateway link alone; this slot carries
              a home glyph so the two marks stop competing. */}
          <NavLink
            to="/dashboard"
            // Function form: a plain string className suppresses NavLink's
            // own `active` class, and the home glyph needs it to light up.
            className={({ isActive }) =>
              isActive ? 'brand-mark active' : 'brand-mark'
            }
            aria-label="Command Center"
          >
            <span className="brand-icon">
              <Home size={17} aria-hidden="true" />
            </span>
            <span className="brand-text">
              <strong>Project</strong>
              <span>Command Center</span>
            </span>
          </NavLink>
        </div>

        <nav className="shell-nav" aria-label="Primary navigation">
          {topbarNav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon size={17} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <CommandSearch />

        <NavLink
          to="/trash"
          className={navClass}
          aria-label={trashCount > 0 ? `Trash (${trashCount} items)` : 'Trash'}
          title="Trash"
        >
          <Trash2 size={17} aria-hidden="true" />
          {trashCount > 0 && (
            <span className="nav-count-badge" aria-hidden="true">
              {trashCount}
            </span>
          )}
        </NavLink>
      </header>

      <div className="app-main">{children}</div>
    </div>
  )
}
