import { AlertTriangle, CalendarClock, GitBranch } from 'lucide-react'
import type { Task } from '../../types/task'
import {
  matchesDashboardSignal,
  type DashboardSignal,
} from './dashboardSignals'

interface SignalDefinition {
  id: DashboardSignal
  label: string
  icon: typeof AlertTriangle
}

const SIGNALS: SignalDefinition[] = [
  { id: 'overdue', label: 'Overdue', icon: AlertTriangle },
  { id: 'blocking', label: 'Blocking', icon: GitBranch },
  { id: 'due_today', label: 'Due today', icon: CalendarClock },
]

interface Props {
  tasks: Task[]
  activeSignal: DashboardSignal | null
  onChange: (signal: DashboardSignal | null) => void
}

export function DashboardSignalStrip({ tasks, activeSignal, onChange }: Props) {
  const counts: Record<DashboardSignal, number> = {
    overdue: tasks.filter((task) => matchesDashboardSignal(task, 'overdue')).length,
    blocking: tasks.filter((task) => matchesDashboardSignal(task, 'blocking')).length,
    due_today: tasks.filter((task) => matchesDashboardSignal(task, 'due_today')).length,
  }

  return (
    <section className="dashboard-signal-strip" aria-label="Task signals">
      {SIGNALS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          className={`dashboard-signal dashboard-signal-${id}${
            activeSignal === id ? ' selected' : ''
          }`}
          aria-label={`${label}: ${counts[id]} ${counts[id] === 1 ? 'task' : 'tasks'}`}
          aria-pressed={activeSignal === id}
          onClick={() => onChange(activeSignal === id ? null : id)}
        >
          <Icon size={16} aria-hidden="true" />
          <span>{label}</span>
          <strong>{counts[id]}</strong>
        </button>
      ))}
    </section>
  )
}
