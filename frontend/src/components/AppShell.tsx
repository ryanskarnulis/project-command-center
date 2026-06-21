import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { useTrashCount } from '../features/trash/TrashCountContext'
import { CommandSearch } from '../features/search/CommandSearch'
import {
  Bell,
  Bot,
  Box,
  ChevronDown,
  ClipboardCheck,
  FolderKanban,
  Gauge,
  HelpCircle,
  LayoutDashboard,
  Library,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Trash2,
} from 'lucide-react'

interface AppShellProps {
  children: ReactNode
}

const primaryNav = [
  { to: '/dashboard', label: 'Command Center', icon: LayoutDashboard },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/training', label: 'Training', icon: ClipboardCheck },
]

const disabledNav = [
  { label: 'AI Assistant', icon: Bot },
  { label: 'Templates', icon: Library },
  { label: 'Integrations', icon: Box },
]

const utilityNav = [
  { to: '/trash', label: 'Trash', icon: Trash2 },
  { to: '/settings', label: 'Settings', icon: Settings },
]

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'shell-nav-link active' : 'shell-nav-link'
}

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()
  const today = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  })

  return (
    <div className="app-shell">
      <aside className="app-sidebar" aria-label="Primary navigation">
        <div className="brand-mark">
          <div className="brand-icon" aria-hidden="true">
            <span />
            <span />
          </div>
          <div>
            <strong>Project</strong>
            <span>Command Center</span>
          </div>
        </div>

        <nav className="shell-nav">
          {primaryNav.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="shell-nav-group" aria-label="Planned tools">
          {disabledNav.map(({ label, icon: Icon }) => (
            <button key={label} className="shell-nav-link disabled" disabled>
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </div>

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
          <button className="shell-nav-link disabled" disabled>
            <HelpCircle size={19} aria-hidden="true" />
            <span>Help & Support</span>
          </button>
        </nav>

        <section className="focus-session" aria-label="Focus mode status">
          <div className="focus-session-icon">
            <Target size={19} aria-hidden="true" />
          </div>
          <div>
            <strong>Focus mode</strong>
            <span>
              <i aria-hidden="true" /> On
            </span>
          </div>
          <button type="button" disabled>
            End session
          </button>
        </section>
      </aside>

      <div className="app-content">
        <header className="topbar">
          <div className="topbar-title">
            <span>{today}</span>
            <strong>Stay focused. Ship impact.</strong>
          </div>

          <CommandSearch />

          <div className="topbar-actions">
            <button type="button" className="icon-button" disabled aria-label="Notifications">
              <Bell size={19} aria-hidden="true" />
              <span className="notification-dot" aria-hidden="true" />
            </button>
            <button type="button" className="icon-button" disabled aria-label="Search">
              <Search size={19} aria-hidden="true" />
            </button>
            <button type="button" className="icon-button" disabled aria-label="Customize">
              <SlidersHorizontal size={19} aria-hidden="true" />
            </button>
            <div className="local-profile" aria-label="Local workspace">
              <ShieldCheck size={18} aria-hidden="true" />
              <span>Local</span>
              <ChevronDown size={15} aria-hidden="true" />
            </div>
          </div>
        </header>

        <div className="app-main">{children}</div>

        <footer className="sync-footer">
          <Gauge size={15} aria-hidden="true" />
          <span>Last synced just now</span>
        </footer>
      </div>
    </div>
  )
}
