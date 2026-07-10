import {
  Fragment,
  useEffect,
  useState,
  type DragEvent,
  type ReactNode,
} from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { listProjects } from '../api/projects'
import { updateTask } from '../api/tasks'
import type { Project } from '../types/project'
import { useToast } from './ToastContext'
import { useTrashCount } from '../features/trash/trashCountContext'
import { useTaskRefresh } from '../features/tasks/taskRefreshContext'
import { TASK_DRAG_TYPE } from '../features/tasks/TaskCard'
import { CommandSearch } from '../features/search/CommandSearch'
import {
  CheckSquare,
  FolderKanban,
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
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
]

const utilityNav = [
  { to: '/trash', label: 'Trash', icon: Trash2 },
]

function navClass({ isActive }: { isActive: boolean }) {
  return isActive ? 'shell-nav-link active' : 'shell-nav-link'
}

function isTaskDrag(e: DragEvent): boolean {
  return e.dataTransfer.types.includes(TASK_DRAG_TYPE)
}

export function AppShell({ children }: AppShellProps) {
  const { count: trashCount } = useTrashCount()
  const { withToast } = useToast()
  const { bump: bumpTaskRefresh } = useTaskRefresh()
  const { pathname } = useLocation()
  const [projects, setProjects] = useState<Project[]>([])
  const [dropProjectId, setDropProjectId] = useState<number | null>(null)

  // Refetch on navigation so newly created/renamed projects show up without a
  // reload — one lightweight local call per route change.
  useEffect(() => {
    let active = true
    listProjects()
      .then((data) => { if (active) setProjects(data) })
      .catch(() => { /* sidebar list is best-effort */ })
    return () => { active = false }
  }, [pathname])

  async function handleDropOnProject(
    project: Project,
    e: DragEvent<HTMLAnchorElement>,
  ): Promise<void> {
    e.preventDefault()
    setDropProjectId(null)
    const raw = e.dataTransfer.getData(TASK_DRAG_TYPE)
    if (!raw) return
    await withToast(updateTask(Number(raw), { project_id: project.id }), {
      success: `Task filed to ${project.name}`,
    })
    bumpTaskRefresh()
  }
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
            <Fragment key={to}>
              <NavLink to={to} className={navClass}>
                <Icon size={19} aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
              {to === '/projects' && projects.length > 0 && (
                <ul className="shell-nav-projects" aria-label="Projects">
                  {projects.map((p) => (
                    <li key={p.id}>
                      <NavLink
                        to={`/projects/${p.id}`}
                        className={({ isActive }) =>
                          `shell-nav-project${isActive ? ' active' : ''}${
                            dropProjectId === p.id ? ' drag-over' : ''
                          }`
                        }
                        onDragOver={(e) => {
                          if (isTaskDrag(e)) {
                            e.preventDefault()
                            e.dataTransfer.dropEffect = 'move'
                            setDropProjectId(p.id)
                          }
                        }}
                        onDragLeave={() =>
                          setDropProjectId((cur) => (cur === p.id ? null : cur))
                        }
                        onDrop={(e) => void handleDropOnProject(p, e)}
                      >
                        <span>{p.name}</span>
                      </NavLink>
                    </li>
                  ))}
                </ul>
              )}
            </Fragment>
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
