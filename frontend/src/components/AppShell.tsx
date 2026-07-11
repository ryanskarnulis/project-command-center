import { type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useTrashCount } from '../features/trash/trashCountContext'
import { CommandSearch } from '../features/search/CommandSearch'
import { Bot, CheckSquare, Sun, Trash2 } from 'lucide-react'

interface AppShellProps {
  children: ReactNode
}

const topbarNav = [
  { to: '/focus', label: 'Focus', icon: Sun },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
  { to: '/agent', label: 'Agent', icon: Bot },
]

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'shell-nav-link active' : 'shell-nav-link'
}

function SpiderIcon() {
  return (
    <svg
      width="26"
      height="26"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {/* legs */}
      <path d="M9.5 12.5 5 9 3.5 4.5" />
      <path d="M9 14 4 13l-2.5-3" />
      <path d="M9 16l-5 1-2 3.5" />
      <path d="M10 17.5 7 21" />
      <path d="M14.5 12.5 19 9l1.5-4.5" />
      <path d="M15 14l5-1 2.5-3" />
      <path d="M15 16l5 1 2 3.5" />
      <path d="M14 17.5 17 21" />
      {/* head + body */}
      <circle cx="12" cy="10.5" r="2" fill="currentColor" stroke="none" />
      <ellipse cx="12" cy="15.5" rx="3" ry="3.6" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()

  return (
    <div className="app-shell">
      <header className="topbar">
        <NavLink to="/dashboard" className="brand-mark" aria-label="Command Center">
          <span className="brand-icon">
            <SpiderIcon />
          </span>
          <span className="brand-text">
            <strong>Project</strong>
            <span>Command Center</span>
          </span>
        </NavLink>

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
