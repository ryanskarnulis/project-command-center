import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays } from 'lucide-react'
import { listAllTasks } from '../../api/tasks'
import { AsyncState } from '../../components/AsyncState'
import type { Task } from '../../types/task'
import { addDaysISO, compareByDue, formatDueDate, todayISO } from '../../utils/dates'

// How far ahead the rail looks, and how many of the soonest tasks it lists.
const LOOKAHEAD_DAYS = 30
const MAX_EVENTS = 5

/**
 * Dashboard rail tile: the next few not-done tasks with a due date in the next
 * month, soonest first. Reads the shared task list and filters client-side —
 * real local data, no external sync.
 */
export function UpcomingEvents() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const start = todayISO()
    const end = addDaysISO(start, LOOKAHEAD_DAYS)
    listAllTasks()
      .then((result) => {
        if (!active) return
        const upcoming = result
          .filter((task) => task.workflow_status !== 'done')
          // Subtasks surface only under their parent, not as their own events.
          .filter((task) => task.parent_task_id === null)
          .filter((task) => task.due_date !== null && task.due_date >= start && task.due_date <= end)
          .sort(compareByDue)
          .slice(0, MAX_EVENTS)
        setTasks(upcoming)
        setError(null)
      })
      .catch((err: unknown) => {
        if (!active) return
        setError(err instanceof Error ? err.message : 'Failed to load upcoming tasks')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  return (
    <section className="panel upcoming-events">
      <div className="section-title">
        <CalendarDays size={18} aria-hidden="true" />
        <h2>Upcoming Events</h2>
      </div>
      <AsyncState
        loading={loading}
        error={error}
        isEmpty={tasks.length === 0}
        loadingLabel="Loading…"
        emptyLabel="Nothing due soon."
      >
        <ul className="upcoming-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <Link to={`/tasks/${task.id}`}>{task.title}</Link>
              <small>{formatDueDate(task.due_date)}</small>
            </li>
          ))}
        </ul>
      </AsyncState>
    </section>
  )
}
