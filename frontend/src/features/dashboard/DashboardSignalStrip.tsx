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
  /** Root tasks the board can actually show — counts stay in sync with it. */
  tasks: Task[]
  activeSignal: DashboardSignal | null
  onChange: (signal: DashboardSignal | null) => void
}

/**
 * One slim line of global counts above the board. Each signal is a toggle:
 * clicking filters every lane to matching cards, clicking again clears.
 */
export function DashboardSignalStrip({ tasks, activeSignal, onChange }: Props) {
  return (
    <section className="dashboard-signal-strip" aria-label="Task signals">
      {SIGNALS.map(({ id, label, icon: Icon }) => {
        const count = tasks.filter((task) =>
          matchesDashboardSignal(task, id),
        ).length
        const selected = activeSignal === id
        return (
          <button
            key={id}
            type="button"
            className={`dashboard-signal dashboard-signal-${id}${
              selected ? ' selected' : ''
            }`}
            aria-label={`${label}: ${count} ${count === 1 ? 'task' : 'tasks'}`}
            aria-pressed={selected}
            onClick={() => onChange(selected ? null : id)}
          >
            <Icon size={16} aria-hidden="true" />
            <span>{label}</span>
            <strong>{count}</strong>
          </button>
        )
      })}
    </section>
  )
}
