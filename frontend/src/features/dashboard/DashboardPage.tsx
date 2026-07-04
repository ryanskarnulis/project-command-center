import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  ClipboardList,
  Clock3,
  FolderKanban,
  Inbox,
  ListChecks,
  Plus,
  Target,
} from 'lucide-react'
import type { ProjectOpenTasksRow } from '../../types/dashboard'
import type { Task } from '../../types/task'
import { compareByDue, dueStatus, formatDueDate } from '../../utils/dates'
import { projectStatus, type Tone } from '../../utils/projectStatus'
import { InboxCapturePanel } from '../inbox/InboxCapturePanel'
import { UpcomingEvents } from './UpcomingEvents'
import { useDashboard } from './useDashboard'

interface MetricCardProps {
  icon: LucideIcon
  title: string
  value: string | number
  detail: string
  tone: Tone
  to: string
  action: string
  cornerIcon?: LucideIcon
  cornerLabel?: string
  cornerTo?: string
  children?: React.ReactNode
}

interface Insight {
  icon: LucideIcon
  title: string
  detail: string
  tone: Tone
  to: string
}

function MetricCard({
  icon: Icon,
  title,
  value,
  detail,
  tone,
  to,
  action,
  cornerIcon: CornerIcon,
  cornerLabel,
  cornerTo,
  children,
}: MetricCardProps) {
  const navigate = useNavigate()
  return (
    <Link to={to} className="metric-card" aria-label={`${title}: ${action}`}>
      {CornerIcon && cornerTo && (
        <button
          type="button"
          className="metric-corner-action"
          aria-label={cornerLabel ?? action}
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            navigate(cornerTo)
          }}
        >
          <CornerIcon size={17} aria-hidden="true" />
        </button>
      )}
      <div className={`metric-icon tone-${tone}`}>
        <Icon size={26} aria-hidden="true" />
      </div>
      <div className="metric-content">
        <span>{title}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
        {children}
      </div>
      <div className="metric-footer">
        <span>{action}</span>
        <ArrowRight size={16} aria-hidden="true" />
      </div>
    </Link>
  )
}

function DueSoonFocusCard({
  tasks,
  todayCount,
  weekCount,
  overdueCount,
}: {
  tasks: Task[]
  todayCount: number
  weekCount: number
  overdueCount: number
}) {
  return (
    <Link to="/today" className="metric-card focus-due-card">
      <div className="metric-icon tone-green">
        <ListChecks size={26} aria-hidden="true" />
      </div>
      <div className="metric-content">
        <span>Today&apos;s Tasks / Due Soon</span>
        <strong>{tasks.length}</strong>
        <small>
          {todayCount} today · {weekCount} this week · {overdueCount} overdue
        </small>
        {tasks.length > 0 ? (
          <ul className="mini-task-list">
            {tasks.slice(0, 2).map((task) => (
              <li key={task.id}>
                <span>{task.title}</span>
                {task.due_date && (
                  <small className={`due due-${dueStatus(task.due_date)}`}>
                    {formatDueDate(task.due_date)}
                  </small>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <small>No due dates in the next week.</small>
        )}
      </div>
      <div className="metric-footer">
        <span>View due work</span>
        <ArrowRight size={16} aria-hidden="true" />
      </div>
    </Link>
  )
}

function tasksForProject(tasks: Task[], projectId: number): Task[] {
  return tasks.filter((task) => task.project_id === projectId)
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`
}

function topBlockingTasks(tasks: Task[]): Task[] {
  return tasks
    .filter((task) => task.is_blocking && task.workflow_status !== 'done')
    .sort(
      (a, b) =>
        b.blocked_task_count - a.blocked_task_count ||
        a.title.localeCompare(b.title) ||
        b.id - a.id,
    )
}

function buildInsights(
  tasks: Task[],
  pendingReview: number,
): Insight[] {
  const blocking = topBlockingTasks(tasks)
  const blocked = tasks.filter(
    (task) =>
      task.is_blocked && !task.is_blocking && task.workflow_status !== 'done',
  )
  const overdue = tasks.filter((task) => dueStatus(task.due_date) === 'overdue')
  const dueSoon = tasks.filter((task) => {
    const status = dueStatus(task.due_date, 7)
    return status === 'today' || status === 'soon'
  })
  const insights: Insight[] = []

  if (blocking.length > 0) {
    const downstream = blocking.reduce(
      (sum, task) => sum + task.blocked_task_count,
      0,
    )
    insights.push({
      icon: AlertTriangle,
      title: pluralize(blocking.length, 'root blocker'),
      detail: `${pluralize(downstream, 'downstream task')} waiting on them.`,
      tone: 'red',
      to: '/tasks?status=blocking',
    })
  }
  if (blocked.length > 0) {
    insights.push({
      icon: AlertTriangle,
      title: pluralize(blocked.length, 'waiting task'),
      detail: 'These are blocked by unfinished dependencies.',
      tone: 'neutral',
      to: '/tasks?status=blocked',
    })
  }
  if (overdue.length > 0) {
    insights.push({
      icon: Clock3,
      title: `${overdue.length} overdue ${overdue.length === 1 ? 'task' : 'tasks'}`,
      detail: 'Review due dates or clear the oldest work first.',
      tone: 'orange',
      to: '/tasks',
    })
  }
  if (pendingReview > 0) {
    insights.push({
      icon: Inbox,
      title: `${pendingReview} capture${pendingReview === 1 ? '' : 's'} awaiting review`,
      detail: 'Accept or reject AI suggestions to keep training data clean.',
      tone: 'blue',
      to: '/inbox',
    })
  }
  if (dueSoon.length > 0) {
    insights.push({
      icon: Target,
      title: 'Focus opportunity',
      detail: `You have ${dueSoon.length} task${dueSoon.length === 1 ? '' : 's'} due soon.`,
      tone: 'green',
      to: '/tasks',
    })
  }
  if (insights.length === 0) {
    insights.push({
      icon: CheckCircle2,
      title: 'Command center is clear',
      detail: 'No blocking work, overdue work, or pending captures right now.',
      tone: 'green',
      to: '/tasks',
    })
  }
  return insights.slice(0, 4)
}

function projectWorkloadWidth(row: ProjectOpenTasksRow, maxOpenTasks: number): string {
  if (row.open_task_count === 0 || maxOpenTasks === 0) return '8%'
  return `${Math.max(14, Math.round((row.open_task_count / maxOpenTasks) * 100))}%`
}

export function DashboardPage() {
  const { overview, tasks, loading, refreshing, error, reload } = useDashboard()
  const [pendingReviewCount, setPendingReviewCount] = useState<number | null>(null)
  const pendingCount = pendingReviewCount ?? 0

  const dashboard = useMemo(() => {
    const blockingTasks = topBlockingTasks(tasks)
    const downstreamBlockedCount = blockingTasks.reduce(
      (sum, task) => sum + task.blocked_task_count,
      0,
    )
    const dueTasks = [...tasks]
      .filter((task) => dueStatus(task.due_date, 7) !== 'none')
      // Subtasks appear only nested under their parent, not in the due list.
      .filter((task) => task.parent_task_id === null)
      .sort(compareByDue)
    const todayCount = tasks.filter(
      (task) => dueStatus(task.due_date) === 'today',
    ).length
    const overdueCount = tasks.filter(
      (task) => dueStatus(task.due_date) === 'overdue',
    ).length
    const weekCount = tasks.filter((task) => {
      const status = dueStatus(task.due_date, 7)
      return status === 'today' || status === 'soon'
    }).length
    return {
      blockingTasks,
      downstreamBlockedCount,
      dueTasks,
      todayCount,
      overdueCount,
      weekCount,
      insights: buildInsights(tasks, pendingCount),
    }
  }, [tasks, pendingCount])

  if (loading) {
    return (
      <div className="dashboard">
        <div className="page-loading">Loading dashboard...</div>
      </div>
    )
  }
  if (error) {
    return (
      <div className="dashboard">
        <p className="error">Error: {error}</p>
      </div>
    )
  }
  if (!overview) return null

  const maxOpenTasks = Math.max(
    0,
    ...overview.projects.map((project) => project.open_task_count),
  )
  const hour = new Date().getHours()
  const greeting =
    hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  return (
    <div className="dashboard" aria-busy={refreshing}>
      <section className="dashboard-hero">
        <div>
          <h1>{greeting}</h1>
          <p>Mission-critical work, captures, and due dates in one place.</p>
        </div>
      </section>

      <InboxCapturePanel
        title="Capture Tasks"
        description="Paste messy text here. Extracted task candidates will show below for approval."
        className="panel command-inbox-panel"
        headingLevel={2}
        onPendingCountChange={setPendingReviewCount}
        onTasksChanged={reload}
      />

      <section className="focus-panel" aria-labelledby="focus-now-heading">
        <div className="section-heading">
          <div className="section-title">
            <span className="heading-icon tone-blue">
              <Target size={20} aria-hidden="true" />
            </span>
            <div>
              <h2 id="focus-now-heading">Focus Now</h2>
              <p>Your mission-critical overview</p>
            </div>
          </div>
        </div>

        <div className="metric-grid">
          <MetricCard
            icon={ClipboardList}
            title="Open Tasks"
            value={overview.total_open_tasks}
            detail="Accepted work not done"
            tone="blue"
            to="/tasks"
            action="View tasks"
            cornerIcon={Plus}
            cornerLabel="Add task"
            cornerTo="/tasks?new=1"
          />
          <MetricCard
            icon={Clock3}
            title="Awaiting Review"
            value={pendingCount}
            detail="AI captures ready to triage"
            tone="orange"
            to="/inbox"
            action="Review now"
          />
          <MetricCard
            icon={AlertTriangle}
            title="Blocking Work"
            value={dashboard.blockingTasks.length}
            detail={`${pluralize(dashboard.downstreamBlockedCount, 'downstream task')} waiting`}
            tone="red"
            to="/tasks?status=blocking"
            action="View blockers"
          >
            {dashboard.blockingTasks.length > 0 ? (
              <ul className="mini-task-list">
                {dashboard.blockingTasks.slice(0, 2).map((task) => (
                  <li key={task.id}>
                    <span>{task.title}</span>
                    <small>{pluralize(task.blocked_task_count, 'task')}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <small>No root blockers active.</small>
            )}
          </MetricCard>
          <DueSoonFocusCard
            tasks={dashboard.dueTasks}
            todayCount={dashboard.todayCount}
            weekCount={dashboard.weekCount}
            overdueCount={dashboard.overdueCount}
          />
        </div>
      </section>

      <div className="dashboard-layout">
        <div className="dashboard-main-grid">
          <section className="panel projects-overview">
            <div className="section-heading compact">
              <div className="section-title">
                <FolderKanban size={19} aria-hidden="true" />
                <h2>Projects Overview</h2>
              </div>
              <div className="section-heading-actions">
                <Link to="/projects">View all projects</Link>
                <Link to="/projects?new=1" className="icon-link" aria-label="Create project">
                  <Plus size={16} aria-hidden="true" />
                </Link>
              </div>
            </div>
            {overview.projects.length === 0 ? (
              <div className="empty-state">No projects yet.</div>
            ) : (
              <div className="project-table">
                {overview.projects.slice(0, 6).map((row) => {
                  const projectTasks = tasksForProject(tasks, row.project_id)
                  const status = projectStatus(projectTasks, row.open_task_count)
                  return (
                    <div className="project-table-row" key={row.project_id}>
                      <div className="project-name-cell">
                        <span className={`project-avatar tone-${status.tone}`}>
                          <FolderKanban size={18} aria-hidden="true" />
                        </span>
                        <div>
                          <Link
                            to={`/projects/${row.project_id}`}
                            state={{ from: 'dashboard' }}
                          >
                            {row.project_name}
                          </Link>
                          <small>{row.open_task_count} open tasks</small>
                        </div>
                      </div>
                      <div className="workload-cell">
                        <span>Workload</span>
                        <div className="workload-bar" aria-hidden="true">
                          <span
                            style={{
                              width: projectWorkloadWidth(row, maxOpenTasks),
                            }}
                          />
                        </div>
                      </div>
                      <span className={`status-pill tone-${status.tone}`}>
                        {status.label}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          <section className="panel insights-panel">
            <div className="section-heading compact">
              <div className="section-title">
                <Bot size={19} aria-hidden="true" />
                <h2>AI Insights</h2>
              </div>
            </div>
            <ul className="insight-list">
              {dashboard.insights.map(({ icon: Icon, title, detail, tone, to }) => (
                <li key={title}>
                  <span className={`insight-icon tone-${tone}`}>
                    <Icon size={20} aria-hidden="true" />
                  </span>
                  <div>
                    <Link to={to}>{title}</Link>
                    <small>{detail}</small>
                  </div>
                  <ArrowRight size={16} aria-hidden="true" />
                </li>
              ))}
            </ul>
          </section>
        </div>

        <aside className="dashboard-rail" aria-label="Dashboard side rail">
          <UpcomingEvents />
        </aside>
      </div>
    </div>
  )
}
