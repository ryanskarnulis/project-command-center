import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, CalendarDays } from 'lucide-react'
import { getCalendar } from '../../api/calendar'
import { AsyncState } from '../../components/AsyncState'
import type { Task } from '../../types/task'
import { compareByDue, formatDueDate } from '../../utils/dates'
import { toLocalISO } from '../calendar/useCalendar'

// How far ahead the rail looks, and how many of the soonest tasks it lists.
const LOOKAHEAD_DAYS = 30
const MAX_EVENTS = 5

/**
 * Dashboard rail tile: the next few accepted, not-done tasks with a due date,
 * soonest first. Reuses the read-only calendar feed (no extra endpoint) and
 * links through to the full calendar. Replaces the old "Calendar not connected"
 * placeholder — this is real local data, no external sync.
 */
export function UpcomingEvents() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const today = new Date()
    const end = new Date(today)
    end.setDate(end.getDate() + LOOKAHEAD_DAYS)
    getCalendar({ start: toLocalISO(today), end: toLocalISO(end) })
      .then((result) => {
        if (!active) return
        const upcoming = result
          .filter((task) => task.workflow_status !== 'done')
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
      <Link to="/calendar" className="upcoming-view-calendar">
        View calendar
        <ArrowRight size={15} aria-hidden="true" />
      </Link>
    </section>
  )
}
