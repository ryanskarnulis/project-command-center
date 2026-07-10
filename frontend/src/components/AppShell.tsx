import { useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useTrashCount } from '../features/trash/trashCountContext'
import { CommandSearch } from '../features/search/CommandSearch'
import {
  CheckSquare,
  LayoutDashboard,
  Sun,
  Trash2,
} from 'lucide-react'

interface AppShellProps {
  children: ReactNode
}

const primaryNav = [
  { to: '/dashboard', label: 'Command Center', icon: LayoutDashboard },
  { to: '/focus', label: 'Focus', icon: Sun },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
]

const utilityNav = [
  { to: '/trash', label: 'Trash', icon: Trash2 },
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

const SIDEBAR_COLLAPSED_KEY = 'pcc.sidebarCollapsed'

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1',
  )

  function toggleSidebar(): void {
    setCollapsed((cur) => {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, cur ? '0' : '1')
      return !cur
    })
  }

  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  })

  return (
    <div className={`app-shell${collapsed ? ' sidebar-collapsed' : ''}`}>
      <aside className="app-sidebar" aria-label="Primary navigation">
        <div className="brand-mark">
          <button
            type="button"
            className="brand-icon"
            onClick={toggleSidebar}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <SpiderIcon />
          </button>
          {!collapsed && (
            <div>
              <strong>Project</strong>
              <span>Command Center</span>
            </div>
          )}
        </div>

        <nav className="shell-nav">
          {primaryNav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <nav className="shell-nav shell-nav-bottom">
          {utilityNav.map(({ to, label, icon: Icon }) => {
            const showCount = to === '/trash' && trashCount > 0
            return (
              <NavLink
                key={to}
                to={to}
                className={navClass}
                aria-label={showCount ? `${label} (${trashCount} items)` : undefined}
              >
                <Icon size={19} aria-hidden="true" />
                <span>{label}</span>
                {showCount && (
                  <span className="nav-count-badge" aria-hidden="true">
                    {trashCount}
                  </span>
                )}
              </NavLink>
            )
          })}
        </nav>
      </aside>

      <div className="app-content">
        <header className="topbar">
          <div className="topbar-title">
            <span>{today}</span>
            <strong>Stay focused. Ship impact.</strong>
          </div>

          <CommandSearch />
        </header>

        <div className="app-main">{children}</div>
      </div>
    </div>
  )
}
