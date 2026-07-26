import { type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useTrashCount } from '../features/trash/trashCountContext'
import { CommandSearch } from '../features/search/CommandSearch'
import { CheckSquare, Sun, Trash2 } from 'lucide-react'
import { GlitchMark } from './GlitchMark'

interface AppShellProps {
  children: ReactNode
}

const topbarNav = [
  { to: '/focus', label: 'Focus', icon: Sun },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
  { to: '/agent', label: 'Agent', icon: GlitchMark },
]

// The gateway launcher serves the apex of whatever domain served this app
// (tasks.home.example → home.example). On localhost/IP dev there is no
// gateway, so the brand icon falls back to the dashboard link.
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/

function gatewayUrl(): string | null {
  const { hostname, port, protocol } = window.location
  const labels = hostname.split('.')
  if (labels.length <= 1 || IPV4.test(hostname)) return null
  return `${protocol}//${labels.slice(1).join('.')}${port ? `:${port}` : ''}/`
}

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'shell-nav-link active' : 'shell-nav-link'
}

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()
  const gateway = gatewayUrl()
  const brandIcon = <img src="/web.png" alt="" width={26} height={26} />

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark">
          {gateway ? (
            <a className="brand-icon" href={gateway} aria-label="Back to The Web" title="The Web">
              {brandIcon}
            </a>
          ) : (
            <NavLink to="/dashboard" className="brand-icon" aria-label="Command Center">
              {brandIcon}
            </NavLink>
          )}
          <NavLink to="/dashboard" className="brand-text" aria-label="Command Center">
            <strong>Project</strong>
            <span>Command Center</span>
          </NavLink>
        </div>

        <nav className="shell-nav" aria-label="Primary navigation">
          {topbarNav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon size={19} aria-hidden="true" />
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
          <Trash2 size={19} aria-hidden="true" />
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
