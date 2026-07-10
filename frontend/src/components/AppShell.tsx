import {
  Fragment,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { listProjects, reorderProjects } from '../api/projects'
import { updateTask } from '../api/tasks'
import {
  isProjectDrag,
  moveBefore,
  PROJECT_DRAG_TYPE,
} from '../features/projects/projectDrag'
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
  const { withToast } = useToast()
  const { bump: bumpTaskRefresh, version: taskRefreshVersion } = useTaskRefresh()
  const { pathname } = useLocation()
  const [projects, setProjects] = useState<Project[]>([])
  const [dropProjectId, setDropProjectId] = useState<number | null>(null)
  const [draggedProjectId, setDraggedProjectId] = useState<number | null>(null)
  // Distinguishes a completed reorder drop from a cancelled drag in onDragEnd.
  const projectDropCommitted = useRef(false)
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1',
  )

  function toggleSidebar(): void {
    setCollapsed((cur) => {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, cur ? '0' : '1')
      return !cur
    })
  }

  // Refetch on navigation (newly created/renamed projects) and on cross-page
  // refresh bumps (e.g. the dashboard board reordering projects).
  useEffect(() => {
    let active = true
    listProjects()
      .then((data) => { if (active) setProjects(data) })
      .catch(() => { /* sidebar list is best-effort */ })
    return () => { active = false }
  }, [pathname, taskRefreshVersion])

  async function commitProjectOrder(): Promise<void> {
    projectDropCommitted.current = true
    setDraggedProjectId(null)
    try {
      // `projects` already holds the drag-preview order.
      const saved = await withToast(
        reorderProjects(projects.map((p) => p.id)),
        { success: 'Projects reordered' },
      )
      setProjects(saved)
      bumpTaskRefresh()
    } catch {
      // Conflict/network: fall back to the server's order.
      listProjects().then(setProjects).catch(() => {})
    }
  }

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
                          }${draggedProjectId === p.id ? ' dragging' : ''}`
                        }
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData(PROJECT_DRAG_TYPE, String(p.id))
                          e.dataTransfer.effectAllowed = 'move'
                          projectDropCommitted.current = false
                          setDraggedProjectId(p.id)
                        }}
                        onDragEnd={() => {
                          setDraggedProjectId(null)
                          // Cancelled drag: discard the preview order.
                          if (!projectDropCommitted.current) {
                            listProjects().then(setProjects).catch(() => {})
                          }
                        }}
                        onDragOver={(e) => {
                          if (isTaskDrag(e)) {
                            e.preventDefault()
                            e.dataTransfer.dropEffect = 'move'
                            setDropProjectId(p.id)
                          } else if (draggedProjectId !== null) {
                            // Live-preview the reorder while hovering.
                            e.preventDefault()
                            e.dataTransfer.dropEffect = 'move'
                            setProjects((cur) =>
                              moveBefore(cur, (x) => x.id, draggedProjectId, p.id),
                            )
                          }
                        }}
                        onDragLeave={() =>
                          setDropProjectId((cur) => (cur === p.id ? null : cur))
                        }
                        onDrop={(e) => {
                          if (isProjectDrag(e)) {
                            e.preventDefault()
                            void commitProjectOrder()
                          } else {
                            void handleDropOnProject(p, e)
                          }
                        }}
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
