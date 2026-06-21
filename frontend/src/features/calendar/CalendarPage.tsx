import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { AsyncState } from '../../components/AsyncState'
import type { Task } from '../../types/task'
import { dueStatus } from '../../utils/dates'
import { toLocalISO, useCalendar, type CalendarView } from './useCalendar'
import './calendar.css'

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const DUE_CLASS: Record<string, string> = {
  overdue: 'due-overdue',
  today: 'due-today',
  soon: 'due-soon',
  none: 'due-none',
}

/** Every day (as Date) from start to end inclusive. */
function daysInRange(start: Date, end: Date): Date[] {
  const days: Date[] = []
  const cursor = new Date(start)
  while (cursor <= end) {
    days.push(new Date(cursor))
    cursor.setDate(cursor.getDate() + 1)
  }
  return days
}

function bucketByDay(tasks: Task[]): Map<string, Task[]> {
  const map = new Map<string, Task[]>()
  for (const task of tasks) {
    if (!task.due_date) continue
    const list = map.get(task.due_date)
    if (list) list.push(task)
    else map.set(task.due_date, [task])
  }
  return map
}

function TaskChip({ task }: { task: Task }) {
  const tone = task.workflow_status === 'done' ? 'none' : dueStatus(task.due_date)
  return (
    <Link
      to={`/tasks/${task.id}`}
      className={`calendar-chip ${DUE_CLASS[tone]} ${
        task.workflow_status === 'done' ? 'calendar-chip-done' : ''
      }`}
      title={task.title}
    >
      <span className={`calendar-chip-dot priority-${task.priority}`} aria-hidden="true" />
      <span className="calendar-chip-title">{task.title}</span>
    </Link>
  )
}

function ViewToggle({
  view,
  onChange,
}: {
  view: CalendarView
  onChange: (view: CalendarView) => void
}) {
  return (
    <div className="calendar-view-toggle" role="group" aria-label="Calendar view">
      {(['month', 'week'] as const).map((option) => (
        <button
          key={option}
          type="button"
          className={view === option ? 'active' : ''}
          aria-pressed={view === option}
          onClick={() => onChange(option)}
        >
          {option === 'month' ? 'Month' : 'Week'}
        </button>
      ))}
    </div>
  )
}

export function CalendarPage() {
  const {
    tasks,
    loading,
    error,
    anchor,
    view,
    range,
    setView,
    goToPrev,
    goToNext,
    goToToday,
  } = useCalendar()

  const days = useMemo(() => daysInRange(range.start, range.end), [range])
  const byDay = useMemo(() => bucketByDay(tasks), [tasks])
  const todayISO = toLocalISO(new Date())
  const anchorMonth = anchor.getMonth()

  const heading = anchor.toLocaleDateString(undefined, {
    month: 'long',
    year: 'numeric',
  })

  return (
    <div className="page calendar-page">
      <header className="page-header">
        <div className="page-title">
          <CalendarDays size={22} aria-hidden="true" />
          <div>
            <h1>Calendar</h1>
            <p className="page-subtitle">Tasks by due date — read-only.</p>
          </div>
        </div>
        <div className="calendar-controls">
          <button type="button" className="calendar-today-button" onClick={goToToday}>
            Today
          </button>
          <div className="calendar-nav">
            <button type="button" aria-label="Previous" onClick={goToPrev}>
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <span className="calendar-heading">{heading}</span>
            <button type="button" aria-label="Next" onClick={goToNext}>
              <ChevronRight size={18} aria-hidden="true" />
            </button>
          </div>
          <ViewToggle view={view} onChange={setView} />
        </div>
      </header>

      <AsyncState
        loading={loading}
        error={error}
        isEmpty={tasks.length === 0}
        loadingLabel="Loading calendar…"
        emptyLabel="No tasks due in this range."
      >
        <div className="calendar-grid" role="grid" aria-label="Calendar">
          {WEEKDAYS.map((label) => (
            <div key={label} className="calendar-weekday" role="columnheader">
              {label}
            </div>
          ))}
          {days.map((day) => {
            const iso = toLocalISO(day)
            const dayTasks = byDay.get(iso) ?? []
            const outside = view === 'month' && day.getMonth() !== anchorMonth
            return (
              <div
                key={iso}
                role="gridcell"
                className={`calendar-cell${outside ? ' calendar-cell-outside' : ''}${
                  iso === todayISO ? ' calendar-cell-today' : ''
                }`}
              >
                <span className="calendar-date">{day.getDate()}</span>
                <div className="calendar-cell-tasks">
                  {dayTasks.map((task) => (
                    <TaskChip key={task.id} task={task} />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </AsyncState>
    </div>
  )
}
